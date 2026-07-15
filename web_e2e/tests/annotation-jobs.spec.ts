import { test, expect, Page } from '@playwright/test';
import { PipelineEditor } from '../pages/pipeline-editor.page';
import { AnnotationJobs } from '../pages/annotation-jobs.page';
import * as utils from '../utils';
import { scanCSV } from 'nodejs-polars';
import * as fs from 'fs';

test.describe('Create job tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    await AnnotationJobs.open(page);
  });

  test('should create job with vcf file', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();

    await expect(jobs.result).toBeVisible({timeout: 15000});
    await expect(jobs.newJobSection).toBeVisible();
    await expect(jobs.jobCreation).not.toBeVisible();
  });

  test('should check if create button is disabled when no file is uploaded', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await expect(jobs.createButton).toBeDisabled();
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await expect(jobs.createButton).toBeEnabled();
  });

  test('should check if create button is enabled when no pipeline is selected', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await expect(jobs.createButton).toBeEnabled();

    await new PipelineEditor(page).newPipeline();
    await expect(jobs.createButton).toBeEnabled();
  });

  test('should check if create button is disabled when pipeline is invalid', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await expect(jobs.createButton).toBeEnabled();

    await utils.typeInPipelineEditor(page, 'invalid content');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });

    await expect(jobs.createButton).toBeDisabled();
  });

  test('should check if create button is disabled when uploaded file is removed', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await expect(jobs.createButton).toBeEnabled();
    await jobs.deleteUploadedFile.click();
    await expect(jobs.createButton).toBeDisabled();
  });

  test('should create job and then delete it', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await customDefaultPipeline(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();

    const lastJobId = await jobs.jobsTable.locator('.job-name').evaluate(el => el.textContent);
    await expect(page.getByText(lastJobId)).toBeVisible();

    await jobs.deleteIcons.nth(0).click();
    await expect(page.getByText(lastJobId)).not.toBeVisible();
  });

  test('should create job with tsv file and columns selected by default', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await customDefaultPipeline(page);

    await jobs.uploadFile('./fixtures/input-tsv-file.tsv');

    await expect(jobs.columnSpecifying).toBeVisible();

    await jobs.create();
    await jobs.waitForJobStatus(utils.successBackgroundColor);
  });

  test('should create job with csv file and columns selected by default', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await customDefaultPipeline(page);

    await jobs.uploadFile('./fixtures/input-csv-file.csv');
    await page.waitForSelector('#table');

    await jobs.create();
    await jobs.waitForJobStatus(utils.successBackgroundColor);
  });
});

test.describe('Job details tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    await AnnotationJobs.open(page);
  });

  test('should check job details of the first job', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();

    await jobs.waitForJobStatus(utils.successBackgroundColor);

    await jobs.openJobDetails();
    await expect(jobs.jobDetails).toBeVisible();
    await expect(jobs.jobDetails.locator('.owner')).not.toBeEmpty();
    await expect(jobs.jobDetails.locator('.name')).not.toBeEmpty();
    await expect(jobs.jobDetails.locator('.date')).not.toBeEmpty();
    await expect(jobs.jobDetails.locator('.time')).not.toBeEmpty();
    await expect(jobs.jobDetails.locator('.started')).not.toBeEmpty();
    await expect(jobs.jobDetails.locator('.duration')).not.toBeEmpty();
    await expect(jobs.jobStatusLabel).not.toBeEmpty();
    await expect(jobs.downloadInput).toBeVisible();
    await expect(jobs.downloadConfig).toBeVisible();
    await expect(jobs.downloadAnnotated).toBeVisible();
    await expect(jobs.jobDetails.locator('#data-size')).toBeVisible();
  });

  test('should download uploaded file from job details modal', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await customDefaultPipeline(page);
    await jobs.uploadFile('./fixtures/input-vcf-file-reduced.vcf');
    await jobs.create();

    await jobs.waitForJobStatus(utils.successBackgroundColor);

    await jobs.openJobDetails();

    const downloadPromise = page.waitForEvent('download');
    await jobs.downloadInput.click();
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/input-vcf-file-reduced.vcf', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should download pipeline config file', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await customDefaultPipeline(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();
    await jobs.waitForJobStatus(utils.successBackgroundColor);

    await jobs.openJobDetails();

    const downloadPromise = page.waitForEvent('download');
    await jobs.downloadConfig.click();
    const downloadedFile = await downloadPromise;

    const downloadData = scanCSV(await downloadedFile.path());
    const fixtureData = scanCSV('./fixtures/custom-pipeline.yaml');
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should download annotated file', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await customDefaultPipeline(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();
    await jobs.waitForJobStatus(utils.successBackgroundColor);

    await jobs.openJobDetails();

    const downloadPromise = page.waitForEvent('download');
    await jobs.downloadAnnotated.click();
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/job-result-1.vcf', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should check job details modal of failed job', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await jobs.uploadFile('./fixtures/input-csv-file.csv');

    await expect(jobs.columnSpecifying).toBeVisible();
    await jobs.selectColumnType('POS', 'vcf_like');

    await jobs.create();
    await jobs.waitForJobStatus(utils.failedBackgroundColor);

    await jobs.openJobDetails();
    await expect(jobs.jobStatusLabel).toHaveText('failed');
    await expect(jobs.downloadInput).toBeVisible();
    await expect(jobs.downloadConfig).toBeVisible();
    await expect(jobs.downloadAnnotated).not.toBeVisible();
  });
});

test.describe('Jobs table tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    await AnnotationJobs.open(page);
  });

  test('should create job and check first row', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await customDefaultPipeline(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();

    await jobs.waitForJobStatus(utils.successBackgroundColor);
    await expect(jobs.jobNames.nth(0)).not.toBeEmpty();
    await expect(jobs.actions.nth(0)).not.toBeEmpty();
  });

  test('should download from table when annotation is success', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await customDefaultPipeline(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();
    await jobs.waitForJobStatus(utils.successBackgroundColor);

    const downloadPromise = page.waitForEvent('download');
    await jobs.downloadIcons.nth(0).click();
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/job-result-2.vcf', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should upload tsv file and check specify columns component content', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-tsv-file.tsv');

    await expect(jobs.columnSpecifying).toBeVisible();
    await expect(jobs.columnHeaderSelect('CHROM')).toHaveText('chrom');
    await expect(jobs.columnHeaderSelect('POS')).toHaveText('pos');
    await expect(jobs.columnHeaderSelect('REF')).toHaveText('ref');
    await expect(jobs.columnHeaderSelect('ALT')).toHaveText('alt');

    await expect(jobs.instructions).toBeVisible();

    // row 1 of input file
    await expect(jobs.cells.nth(0)).toHaveText('chr1');
    await expect(jobs.cells.nth(1)).toHaveText('85827');
    await expect(jobs.cells.nth(2)).toHaveText('T');
    await expect(jobs.cells.nth(3)).toHaveText('C');

    //row 2 of input file
    await expect(jobs.cells.nth(4)).toHaveText('chr1');
    await expect(jobs.cells.nth(5)).toHaveText('183733');
    await expect(jobs.cells.nth(6)).toHaveText('C');
    await expect(jobs.cells.nth(7)).toHaveText('T');
  });

  test('should show error message when specifying invalid combination of columns', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-csv-file.csv');

    await jobs.selectColumnType('CHROM', 'variant');

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
    await AnnotationJobs.open(page);
  });

  test('should check if create button is disabled when invalid file is uploaded', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/invalid-input-file-format.yaml');
    await expect(page.getByText('Unsupported format!')).toBeVisible();
    await expect(jobs.createButton).toBeDisabled();
    await expect(jobs.columnSpecifying).not.toBeVisible();
    await expect(jobs.separatorList).not.toBeVisible();
  });

  test('should upload invalid vcf file', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await jobs.uploadFile('./fixtures/invalid-vcf-input-file.vcf');

    await jobs.create();
    await expect(page.getByText('does not have valid header')).toBeVisible();
  });

  test('should expect error message file with invalid separator is uploaded', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/invalid-separator.csv');
    await jobs.selectColumnType('CHROM+POS+REF+ALT', 'chrom');
    await expect(page.getByText('Cannot build annotatable from selected columns!')).toBeVisible();
    await expect(jobs.createButton).toBeDisabled();
  });

  test('should expect error message if file content is not separeted correctly', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/wrongly-separated-row.csv');
    await jobs.selectColumnType('CHROM,POS,REF,ALT', 'chrom');
    await expect(page.getByText('Cannot build annotatable from selected columns!')).toBeVisible();
    await expect(jobs.createButton).toBeDisabled();
  });

  test('should expect error message when no columns are specified', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/wrongly-separated-row.csv');
    await expect(page.getByText('No columns selected!')).toBeVisible();
    await expect(jobs.createButton).toBeDisabled();
  });

  test.skip('should upload file with more than 1000 variants', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/more-than-1000.vcf');
    await jobs.create();
    await expect(page.getByText('Upload limit reached!')).toBeVisible();
  });
});

test.describe('Job file upload tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await AnnotationJobs.open(page);
  });

  test('should upload VCF file via drag-and-drop', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.dropFile(fs.readFileSync('./fixtures/input-vcf-file.vcf'), 'input-vcf-file.vcf', 'text/vcard');

    await expect(jobs.uploadedFileContainer).toBeVisible();
    await expect(jobs.fileInfo).toContainText('input-vcf-file.vcf');
    await expect(jobs.createButton).toBeEnabled();
  });

  test('should reject unsupported format via drag-and-drop', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.dropFile(fs.readFileSync('./fixtures/invalid-input-file-format.yaml'), 'invalid-file.yaml');

    await expect(jobs.uploadedFileContainer).toBeVisible();
    await expect(page.getByText('Unsupported format!')).toBeVisible();
    await expect(jobs.createButton).toBeDisabled();
  });

  test('should switch separator from tab to comma for TSV file', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-tsv-file.tsv');
    await page.waitForSelector('#table');

    await expect(jobs.tabSeparatorRadio).toBeChecked();
    await expect(jobs.commaSeparatorRadio).not.toBeChecked();

    const separatorResponse = page.waitForResponse(
      resp => resp.url().includes('api/jobs/preview')
    );
    await jobs.commaSeparatorRadio.click();
    await separatorResponse;

    await expect(jobs.commaSeparatorRadio).toBeChecked();
    await expect(jobs.tabSeparatorRadio).not.toBeChecked();
  });

  test('should switch separator from comma to tab for CSV file', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-csv-file.csv');
    await page.waitForSelector('#table');

    await expect(jobs.commaSeparatorRadio).toBeChecked();
    await expect(jobs.tabSeparatorRadio).not.toBeChecked();

    const separatorResponse = page.waitForResponse(
      resp => resp.url().includes('api/jobs/preview')
    );
    await jobs.tabSeparatorRadio.click();
    await separatorResponse;

    await expect(jobs.tabSeparatorRadio).toBeChecked();
    await expect(jobs.commaSeparatorRadio).not.toBeChecked();
  });

  test('should show genome selector when location column is auto-mapped', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-location-column-file.tsv');
    await page.waitForSelector('#table');

    await expect(jobs.selectGenome).toBeVisible();
    await expect(page.locator('label[for="select-genome"]')).toHaveText('Select genome:');

    const options = await jobs.selectGenome.locator('option').allTextContents();
    expect(options.length).toBe(7);
  });
});


async function customDefaultPipeline(page: Page): Promise<void> {
  const editor = new PipelineEditor(page);
  await editor.newPipeline();
  await expect(editor.pipelineInput).toBeEmpty();
  await expect(editor.monacoEditor.nth(0)).toBeEmpty();

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

  await PipelineEditor.waitForLoaded(page);
}

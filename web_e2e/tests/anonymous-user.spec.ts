import { test, expect, Page } from '@playwright/test';
import { scanCSV } from 'nodejs-polars';
import * as utils from '../utils';

test.describe('Anonymous user tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    // wait for default pipeline to load
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should check if delete and save pipelines buttons are hidden', async({ page }) => {
    await expect(page.getByRole('button', { name: 'Save' })).not.toBeVisible();
    await expect(page.getByRole('button', { name: 'Save as' })).not.toBeVisible();
    await expect(page.getByRole('button', { name: 'Delete' })).not.toBeVisible();
  });

  test('should append gene set annotator', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('gene_set_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="resource_id-dropdown"]').click();
    await page.locator('mat-option').getByText('gene_properties/gene_sets/spark').click();
    await page.locator('[id="input_gene_list-dropdown"]').click();
    await page.locator('mat-option').getByText('gene_list').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('.attribute-source')).toHaveCount(1);

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/validate')
      ),
    ]);

    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const value = await page.evaluate(() => {
      // eslint-disable-next-line max-len
      // eslint-disable-next-line @typescript-eslint/no-unsafe-return, @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-unsafe-call, @typescript-eslint/no-explicit-any
      return (window as any).monaco.editor.getModels()[0].getValue();
    });

    expect(value).toContain(
      '- gene_set_annotator:\n'+
      '    resource_id: gene_properties/gene_sets/spark\n' +
      '    input_gene_list: gene_list\n'+
      '    attributes:\n'+
      '    - name: in_sets\n'+
      '      source: in_sets\n'+
      '      internal: false\n'
    );
  });

  test('should use anonynmous pipeline for single annotation', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await expect(page.locator('#pipelines-input')).toBeEmpty();
    await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );

    await utils.typeInPipelineEditor(
      page,
      '- effect_annotator:\n' +
      '    attributes:\n' +
      '    - internal: false\n' +
      '      name: worst_effect\n' +
      '      source: worst_effect\n' +
      '    - internal: false\n' +
      '      name: worst_effect_genes\n' +
      '      source: worst_effect_genes\n' +
      '    - internal: false\n' +
      '      name: gene_effects\n' +
      '      source: gene_effects\n' +
      '    - internal: false\n' +
      '      name: effect_details\n' +
      '      source: effect_details\n' +
      '    - internal: true\n' +
      '      name: gene_list\n' +
      '      source: gene_list\n' +
      '    gene_models: hg19/gene_models/refGene_v201309\n' +
      '    genome: hg38/genomes/GRCh38-hg38'
    );

    await saveResponse;

    await page.locator('#examples-button').click();
    await page.getByRole('menuitem', {name: 'chr1 11796321 G A', exact: true}).click();
    await page.waitForSelector('#report', {timeout: 120000});
    await expect(page.locator('#report')).toBeVisible();

    await expect(page.locator('#history-table')).not.toBeVisible();
  });

  test('should use public pipeline for single annotation', async({ page }) => {
    await page.locator('#pipelines-input').click();
    await page.locator('mat-option').getByText('pipeline/T2T_Clinical_annotation').click();

    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', {name: 'Go'}).click();
    await page.waitForSelector('#report', {timeout: 120000});
    await expect(page.locator('#report')).toBeVisible();
  });

  test('should download single annotation report', async({ page }) => {
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await page.locator('.dropdown-icon').click();
    await page.locator('mat-option').getByText('pipeline/T2T_Clinical_annotation').click();

    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', {name: 'Go'}).click();
    await page.waitForSelector('#report', {timeout: 120000});
    await expect(page.locator('#download-report-button')).toBeVisible();

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#download-report-button').click();
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/chr1_1265232_G_A_report.tsv', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should use public pipeline for job annotation', async({ page }) => {
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file-reduced.vcf');
    await page.locator('#create-button').click();

    await expect(page.getByText('Job name: anonymous_job')).toBeVisible({timeout: 120000});
    await page.waitForSelector('.success-status', {timeout: 120000});

    await expect(page.locator('#history-table')).not.toBeVisible();
  });

  test('should use anonymous pipeline for job annotation', async({ page }) => {
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
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

    await saveResponse;

    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await page.locator('#create-button').click();

    await expect(page.locator('#result')).toBeVisible({ timeout: 120000 });
    await expect(page.locator('#new-job-section')).toBeVisible();
  });

  test('should annotate with tsv file', async({ page }) => {
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
    await customDefaultPipeline(page);
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-tsv-file.tsv');
    await page.locator('#create-button').click();
    await page.waitForSelector('.success-status', {timeout: 120000});
  });

  test('should annotate with csv file', async({ page }) => {
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();

    await customDefaultPipeline(page);
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-csv-file.csv');
    await page.locator('#create-button').click();
    await page.waitForSelector('.success-status', {timeout: 120000});
  });

  test('should download job result', async({ page }) => {
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
    await customDefaultPipeline(page);
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await page.locator('#create-button').click();

    await expect(page.getByText('Job name: anonymous_job')).toBeVisible();
    await page.waitForSelector('.success-status', {timeout: 120000});

    const downloadPromise = page.waitForEvent('download');
    await page.locator('#download-result').click();
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/job-result-3.vcf', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should be able to create new job after the previous one', async({ page }) => {
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await page.locator('#create-button').click();

    await expect(page.locator('#new-job-section')).toBeVisible();
    await page.locator('#new-job-button').click();
    await expect(page.locator('#result')).not.toBeVisible();
    await expect(page.locator('#file-upload-field')).toBeVisible();
  });
});

test.describe('Web socket tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    // wait for default pipeline to load
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should download job result by link copy', async({ page }) => {
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
    await customDefaultPipeline(page);
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await page.locator('#create-button').click();

    await page.waitForSelector('.success-status', {timeout: 120000});

    const downloadUrl = await page.locator('#download-result').getAttribute('href');

    await page.getByRole('link', { name: 'About' }).click();

    const downloadPromise = page.waitForEvent('download');
    // eslint-disable-next-line no-return-assign
    await page.evaluate(url => window.location.href = url ?? '', downloadUrl);
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/job-result-3.vcf', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
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
    '- effect_annotator:\n' +
    '   gene_models: hg38/gene_models/GENCODE/48/basic/ALL\n' +
    '   genome: hg38/genomes/GRCh38.p13\n' +
    '   attributes:\n' +
    '   - worst_effect\n' +
    '   - gene_effects\n' +
    '   - effect_details\n' +
    '   - name: gene_list \n' +
    '     internal: true\n'
  );

  await saveResponse;

  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}
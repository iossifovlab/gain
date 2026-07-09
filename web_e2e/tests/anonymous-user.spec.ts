import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../pages/pipeline-editor.page';
import { SingleAnnotation } from '../pages/single-annotation.page';
import { AnnotationJobs } from '../pages/annotation-jobs.page';
import { scanCSV } from 'nodejs-polars';
import * as utils from '../utils';
import { AnnotatorDialog } from '../pages/annotator.dialog';

test.describe('Anonymous user tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    // wait for default pipeline to load
    await PipelineEditor.waitForLoaded(page);
    await utils.waitForSession(page);
    await utils.deleteAnonymousJobs(page);
    await utils.setAnonymousUserIpQuota(page, 'daily_jobs', 100_000);
    await utils.setAnonymousUserSessionQuota(page, 'daily_jobs', 1_000);
    await utils.setAnonymousUserIpQuota(page, 'daily_variants', 100_000);
    await utils.setAnonymousUserSessionQuota(page, 'daily_variants', 1_000);
    await utils.setAnonymousUserIpQuota(page, 'daily_attributes', 100_000);
    await utils.setAnonymousUserSessionQuota(page, 'daily_attributes', 1_000);
  });

  test('should check if delete and save pipelines buttons are hidden', async({ page }) => {
    await expect(page.getByRole('button', { name: 'Save' })).not.toBeVisible();
    await expect(page.getByRole('button', { name: 'Save as' })).not.toBeVisible();
    await expect(page.getByRole('button', { name: 'Delete' })).not.toBeVisible();
  });

  test('should append gene set annotator', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('gene_set_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('resource_id', 'gene_properties/gene_sets/autism');
    await annotatorModal.selectParameter('input_gene_list', 'gene_list');
    await annotatorModal.next();

    await expect(annotatorModal.attributeSources).toHaveCount(1);

    await Promise.all([
      annotatorModal.finish(),
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
      '    resource_id: gene_properties/gene_sets/autism\n' +
      '    input_gene_list: gene_list\n'+
      '    attributes:\n'+
      '    - name: in_sets\n'+
      '      source: in_sets\n'+
      '      internal: false\n'
    );
  });

  test('should use anonynmous pipeline for single annotation', async({ page }) => {
    const editor = new PipelineEditor(page);
    const singleAnnotation = new SingleAnnotation(page);
    await editor.newPipeline();
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(editor.monacoEditor.nth(0)).toBeEmpty();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );

    await utils.typeInPipelineEditor(
      page,
      '- effect_annotator:\n' +
      '    gene_models: hg38/gene_models/MANE/1.1\n' +
      '    attributes:\n' +
      '    - name: worst_effect\n' +
      '      source: worst_effect\n' +
      '      internal: false\n' +
      '    - name: worst_effect_genes\n' +
      '      source: worst_effect_genes\n' +
      '      internal: false\n' +
      '    - name: gene_effects\n' +
      '      source: gene_effects\n' +
      '      internal: false\n' +
      '    - name: effect_details\n' +
      '      source: effect_details\n' +
      '      internal: false\n' +
      '    - name: gene_list\n' +
      '      source: gene_list\n' +
      '      internal: true\n'
    );

    await saveResponse;

    await singleAnnotation.selectExample('chr1 11796321 G A');
    await singleAnnotation.waitForReport();
    await expect(singleAnnotation.report).toBeVisible();

    await expect(singleAnnotation.historyTable).not.toBeVisible();
  });

  test('should use public pipeline for single annotation', async({ page }) => {
    const editor = new PipelineEditor(page);
    const singleAnnotation = new SingleAnnotation(page);
    await editor.pipelineInput.click();
    await page.locator('mat-option').getByText('pipeline/T2T_clinical_annotation').click();

    await singleAnnotation.annotate('chr1 1265232 G A');
    await expect(singleAnnotation.report).toBeVisible();
  });

  test('should download single annotation report', async({ page }) => {
    const editor = new PipelineEditor(page);
    const singleAnnotation = new SingleAnnotation(page);
    await PipelineEditor.waitForLoaded(page);
    await editor.dropdownIcon.click();
    await page.locator('mat-option').getByText('pipeline/T2T_clinical_annotation').click();

    await singleAnnotation.annotate('chr1 1265232 G A');
    await expect(singleAnnotation.downloadReportButton).toBeVisible();

    const downloadPromise = page.waitForEvent('download');
    await singleAnnotation.downloadReportButton.click();
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true, sep: '\t'});
    const downloadData = scanCSV('./fixtures/chr1_1265232_G_A_report.tsv', {truncateRaggedLines: true, sep: '\t'});
    const fixtureFrame = (await fixtureData.collect()).sort('Attribute name');
    const downloadFrame = (await downloadData.collect()).sort('Attribute name');
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should use public pipeline for job annotation', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await AnnotationJobs.open(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await jobs.uploadFile('./fixtures/input-vcf-file-reduced.vcf');
    await jobs.create();

    await expect(page.getByText('Job name: anonymous_job')).toBeVisible({timeout: 120000});
    await page.waitForSelector('.success-status', {timeout: 120000});

    await expect(page.locator('#history-table')).not.toBeVisible();
  });

  test('should use anonymous pipeline for job annotation', async({ page }) => {
    const editor = new PipelineEditor(page);
    const jobs = new AnnotationJobs(page);
    await AnnotationJobs.open(page);
    await editor.newPipeline();
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(editor.monacoEditor.nth(0)).toBeEmpty();


    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );

    await utils.typeInPipelineEditor(
      page,
      '- normalize_allele_annotator:\n' +
      '    genome: hg38/genomes/GRCh38-hg38\n' +
      '\n' +
      '- allele_score_annotator:\n' +
      '    resource_id: hg38/scores/ClinVar_20240730\n' +
      '    input_annotatable: normalized_allele\n' +
      '    attributes:\n' +
      '    - name: clinical_significance\n' +
      '      source: CLNSIG\n' +
      '    - name: clinical_disease_name\n' +
      '      source: CLNDN \n'
    );

    await saveResponse;

    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();

    await expect(jobs.result).toBeVisible({ timeout: 120000 });
    await expect(jobs.newJobSection).toBeVisible();
  });

  test('should annotate with tsv file', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await AnnotationJobs.open(page);
    await utils.customDefaultPipeline(page);
    await jobs.uploadFile('./fixtures/input-tsv-file.tsv');
    await jobs.create();
    await page.waitForSelector('.success-status', {timeout: 120000});
  });

  test('should annotate with csv file', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await AnnotationJobs.open(page);

    await utils.customDefaultPipeline(page);
    await jobs.uploadFile('./fixtures/input-csv-file.csv');
    await jobs.create();
    await page.waitForSelector('.success-status', {timeout: 120000});
  });

  test('should download job result', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await AnnotationJobs.open(page);
    await utils.customDefaultPipeline(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();

    await expect(page.getByText('Job name: anonymous_job')).toBeVisible();
    await page.waitForSelector('.success-status', {timeout: 120000});

    const downloadPromise = page.waitForEvent('download');
    await jobs.downloadResult.click();
    const downloadedFile = await downloadPromise;

    const fixtureData = scanCSV(await downloadedFile.path(), {truncateRaggedLines: true});
    const downloadData = scanCSV('./fixtures/job-result-3.vcf', {truncateRaggedLines: true});
    const fixtureFrame = await fixtureData.collect();
    const downloadFrame = await downloadData.collect();
    expect(fixtureFrame.toString()).toEqual(downloadFrame.toString());
  });

  test('should be able to create new job after the previous one', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await AnnotationJobs.open(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();

    await expect(jobs.newJobSection).toBeVisible();
    await jobs.newJobButton.click();
    await expect(jobs.result).not.toBeVisible();
    await expect(jobs.fileUploadField).toBeVisible();
  });
});

test.describe('Web socket tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    // wait for default pipeline to load
    await PipelineEditor.waitForLoaded(page);
    await utils.waitForSession(page);
    await utils.deleteAnonymousJobs(page);
  });

  test('should download job result by link copy', async({ page }) => {
    const jobs = new AnnotationJobs(page);
    await AnnotationJobs.open(page);
    await utils.customDefaultPipeline(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();

    await page.waitForSelector('.success-status', {timeout: 120000});

    const downloadUrl = await jobs.downloadResult.getAttribute('href');

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

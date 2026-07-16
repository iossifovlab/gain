import { test, expect } from '@playwright/test';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';
import { AnnotatorDialog } from '../../pages/annotator.dialog';
import { PipelineEditor } from '../../pages/pipeline-editor.page';

test.describe('Pipeline status bar tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
  });

  test('should show annotatable count in status bar', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(editor.monacoEditor.nth(0)).toBeEmpty();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );

    await utils.typeInPipelineEditor(
      page,
      '- normalize_allele_annotator:\n' +
      '    genome: hg38/genomes/GRCh38.p14\n' +
      '\n' +
      '- allele_score:\n' +
      '    resource_id: hg38/scores/dbSNP\n' +
      '    input_annotatable: normalized_allele'
    );

    await saveResponse;

    await PipelineEditor.waitForLoaded(page);
    await expect(editor.statusItem(2)).toContainText('1 annotatables');
  });

  test('should show gene list count in status bar for pipeline with gene list attribute', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), { timeout: 30000 }
    );
    await utils.typeInPipelineEditor(
      page,
      'preamble:\n' +
      '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
      'annotators:\n' +
      '- effect_annotator:\n' +
      '    gene_models: hg38/gene_models/GENCODE/48/basic/ALL\n' +
      '    attributes:\n' +
      '    - worst_effect\n' +
      '    - name: gene_list\n' +
      '      source: gene_list\n' +
      '      internal: true\n'
    );
    await saveResponse;
    await PipelineEditor.waitForLoaded(page);

    await expect(editor.statusItem(3)).toContainText('1 gene list');
  });

  test('should update annotator and attribute counts in status bar after adding annotator', async({ page }) => {
    const editor = new PipelineEditor(page);
    const annotatorModal = new AnnotatorDialog(page);
    await customDefaultPipeline(page);

    await expect(editor.statusItem(0)).toHaveText('menu1 annotators');
    await expect(editor.statusItem(1)).toHaveText('menu_open2 attributes');

    // add simple_effect_annotator which contributes 3 attributes
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('simple_effect_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');
    await annotatorModal.next();

    await Promise.all([
      annotatorModal.finish(),
      page.waitForResponse(resp => resp.url().includes('api/editor/pipeline_status')),
    ]);

    await expect(editor.statusItem(0)).toHaveText('menu2 annotators');
    await expect(editor.statusItem(1)).toHaveText('menu_open5 attributes');
  });

  test('should update status bar counts when editing YAML directly', async({ page }) => {
    const editor = new PipelineEditor(page);
    await customDefaultPipeline(page);

    // Initial state: 1 annotator, 2 attributes
    await expect(editor.statusItem(0)).toHaveText('menu1 annotators');
    await expect(editor.statusItem(1)).toHaveText('menu_open2 attributes');

    // Add another annotator to the existing YAML (append, not replace)
    await editor.monacoEditor.click();
    // Move cursor to end of file
    await page.keyboard.press('Control+End');
    // Add new annotator
    await page.keyboard.type('\n- normalize_allele_annotator:\n    genome: hg38/genomes/GRCh38.p13\n');

    await page.waitForResponse(resp => resp.url().includes('api/editor/pipeline_status'));

    // Verify counts updated: 2 annotators, 3 attributes
    await expect(editor.statusItem(0)).toHaveText('menu2 annotators');
    await expect(editor.statusItem(1)).toHaveText('menu_open3 attributes');
  });

  test('should show all status bar items together with correct values', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), { timeout: 30000 }
    );

    await utils.typeInPipelineEditor(
      page,
      'preamble:\n' +
      '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
      'annotators:\n' +
      '- effect_annotator:\n' +
      '    gene_models: hg38/gene_models/GENCODE/48/basic/ALL\n' +
      '    genome: hg38/genomes/GRCh38.p13\n' +
      '    attributes:\n' +
      '    - worst_effect\n' +
      '    - gene_effects\n' +
      '    - effect_details\n' +
      '    - name: gene_list\n' +
      '      source: gene_list\n' +
      '      internal: true\n' +
      '- allele_score:\n' +
      '    resource_id: hg38/scores/CADD_v1.7\n'
    );

    await saveResponse;
    await PipelineEditor.waitForLoaded(page);

    // Verify all status bar items
    await expect(editor.statusItem(0)).toHaveText('menu2 annotators');
    await expect(editor.statusItem(1)).toHaveText('menu_open6 attributes');
    await expect(editor.statusItem(2)).toHaveText('edit_note0 annotatables');
    await expect(editor.statusItem(3)).toHaveText('grain1 gene list');
  });

  test('should update counts when removing annotators from YAML', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.selectPipeline('pipeline/hg38_clinical_annotation');

    await expect(editor.statusItem(0)).toHaveText('menu13 annotators');
    await expect(editor.statusItem(1)).toHaveText('menu_open22 attributes');

    /* eslint-disable */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      const model = monaco.editor.getModels()[0];

      model.applyEdits([
        {
          // Delete from line 18 (start of the 2nd annotator) to EOF,
          // leaving only the first annotator (the MANE effect_annotator).
          // Uses the model's live line count so it survives the public
          // pipeline growing/shrinking below line 18.
          range: new monaco.Range(18, 1, model.getLineCount(), model.getLineMaxColumn(model.getLineCount())),
          text: ''
        }
      ]);
    });
    /* eslint-enable */

    await page.waitForResponse(resp => resp.url().includes('api/editor/pipeline_status'));

    await expect(editor.statusItem(0)).toHaveText('menu1 annotators');
    await expect(editor.statusItem(1)).toHaveText('menu_open3 attributes');
    await expect(editor.statusItem(2)).toHaveText('edit_note0 annotatables');
    await expect(editor.statusItem(3)).toHaveText('grain0 gene list');
  });

  test('should show all zeros for an empty new pipeline', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(editor.monacoEditor.nth(0)).toBeEmpty();

    await expect(editor.statusItem(0)).toHaveText('menu0 annotators');
    await expect(editor.statusItem(1)).toHaveText('menu_open0 attributes');
    await expect(editor.statusItem(2)).toHaveText('edit_note0 annotatables');
    await expect(editor.statusItem(3)).toHaveText('grain0 gene list');
  });

  test('should keep the last valid counts in the status bar when config becomes invalid', async({ page }) => {
    const editor = new PipelineEditor(page);
    // Start from a valid pipeline so the status bar holds non-zero counts.
    await customDefaultPipeline(page);
    await expect(editor.statusItem(0)).toHaveText('menu1 annotators');
    await expect(editor.statusItem(1)).toHaveText('menu_open2 attributes');

    // Make the config invalid (preamble only, no annotators). The status is only
    // refreshed on a successful validation, so the bar retains the last valid
    // counts while the config is invalid rather than resetting to zero.
    /* eslint-disable */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      const model = monaco.editor.getModels()[0];
      model.setValue('preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    });
    /* eslint-enable */

    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });
    await expect(page.getByText('Invalid configuration')).toBeVisible();

    await expect(editor.statusItem(0)).toHaveText('menu1 annotators');
    await expect(editor.statusItem(1)).toHaveText('menu_open2 attributes');
  });
});

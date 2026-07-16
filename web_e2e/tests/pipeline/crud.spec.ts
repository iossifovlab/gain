import { test, expect } from '@playwright/test';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import { SingleAnnotation } from '../../pages/single-annotation.page';

test.describe('Pipeline tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await PipelineEditor.waitForLoaded(page);
  });

  test('should create new pipeline and save it', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(editor.monacoEditor.nth(0)).toBeEmpty();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );

    await utils.typeInPipelineEditor(
      page,
      'preamble:\n' +
      '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
      'annotators:\n' +
      '- allele_score:\n' +
      '    resource_id: hg38/scores/CADD_v1.7\n'
    );

    await saveResponse;

    await PipelineEditor.waitForLoaded(page);

    await editor.saveAs();

    await expect(editor.nameModal).toBeVisible();
    await editor.saveAsName('My Pipeline');

    await expect(editor.pipelineInput).toHaveValue('My Pipeline');
    await PipelineEditor.waitForLoaded(page);
  });


  test('should create new pipeline and use it without saving it', async({ page }) => {
    const editor = new PipelineEditor(page);
    await PipelineEditor.waitForLoaded(page);
    await editor.newPipeline();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
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

    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.selectExample('chr1 11796321 G A');
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(singleAnnotation.report).toBeVisible({timeout: 120000});
  });

  test('should not be able to save pipeline if invalid', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();
    await utils.typeInPipelineEditor(
      page,
      'preamble:\n' +
      'input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
      'annotators:\n' +
      '- allele_score:\n' +
      'resource_id: hg38/scores/CADD_v1.7'
    );
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });
    await expect(page.getByText('Invalid configuration')).toBeVisible();
    await expect(editor.saveAsButton).toBeDisabled();
  });

  test('should edit public pipeline and annotate with it', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.selectPipeline('pipeline/hg38_clinical_annotation');
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

    await PipelineEditor.waitForLoaded(page);
    await expect(editor.pipelineInput).toBeEmpty();

    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.selectExample('chr1 11796321 G A');
    await expect(singleAnnotation.report).toBeVisible({timeout: 120000});
  });

  test('should edit user pipeline and save it', async({ page }) => {
    const editor = new PipelineEditor(page);
    // create pipeline
    await editor.newPipeline();
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(editor.monacoEditor.nth(0)).toBeEmpty();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );

    await utils.typeInPipelineEditor(
      page,
      'preamble:\n' +
      '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
      'annotators:\n' +
      '- allele_score:\n' +
      '    resource_id: hg38/scores/CADD_v1.7\n'
    );

    await saveResponse;

    await PipelineEditor.waitForLoaded(page);

    await editor.saveAs();

    await expect(editor.nameModal).toBeVisible();
    await editor.saveAsName('My pipeline');

    await expect(editor.pipelineInput).toHaveValue('My pipeline');

    // edit pipeline
    /* eslint-disable */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      const model = monaco.editor.getModels()[0];

      model.applyEdits([
        {
          range: new monaco.Range(6, 1, 13, 1),
          text: '- position_score_annotator:\n' +
                '    resource_id: hg19/scores/FitCons2/E035\n' +
                '    attributes:\n' +
                '    - internal: false\n' +
                '      name: FitCons2_E035\n' +
                '      source: FitCons2_E035\n'
        }
      ]);
    });
    /* eslint-enable */

    await expect(editor.pipelineInput).toHaveValue('My pipeline *');
    await editor.save();
    await expect(editor.pipelineInput).toHaveValue('My pipeline');
  });

  test('should delete user pipeline', async({ page }) => {
    const editor = new PipelineEditor(page);
    // create pipeline
    await editor.newPipeline();
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(editor.monacoEditor.nth(0)).toBeEmpty();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );

    await utils.typeInPipelineEditor(
      page,
      'preamble:\n' +
      '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
      'annotators:\n' +
      '- allele_score:\n' +
      '    resource_id: hg38/scores/CADD_v1.7\n'
    );

    await saveResponse;

    await PipelineEditor.waitForLoaded(page);

    await editor.saveAs();

    await expect(editor.nameModal).toBeVisible();
    await editor.saveAsName('My pipeline');

    await editor.delete();

    await Promise.all([
      editor.confirmDeleteButton.click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
      ),
    ]);
    // The dropdown reverts to the default only after the (un-awaited) post-action
    // GET /api/pipelines returns; under daphne sync-view serialization that GET can
    // exceed the 5s default window (gain#150), so allow the response budget here.
    await expect(editor.pipelineInput).toHaveValue(
      'pipeline/hg38_clinical_annotation', { timeout: 30000 });
  });

  test('should make copy of public pipeline by clicking \'save as\'', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.selectPipeline('pipeline/hg38_clinical_annotation');
    await editor.saveAs();

    await expect(editor.nameModal).toBeVisible();
    await editor.saveAsName('Public pipeline copy');

    await PipelineEditor.waitForLoaded(page);
    await expect(editor.pipelineInput).toHaveValue('Public pipeline copy', { timeout: 30000 });
  });

  test('should make copy of user pipeline by clicking \'save as\'', async({ page }) => {
    const editor = new PipelineEditor(page);
    // create pipeline
    await editor.newPipeline();
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(editor.monacoEditor.nth(0)).toBeEmpty();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );

    await utils.typeInPipelineEditor(
      page,
      'preamble:\n' +
      '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
      'annotators:\n' +
      '- allele_score:\n' +
      '    resource_id: hg38/scores/CADD_v1.7\n'
    );

    await saveResponse;

    await PipelineEditor.waitForLoaded(page);

    await editor.saveAs();

    await expect(editor.nameModal).toBeVisible();
    await editor.saveAsName('My pipeline');

    await editor.saveAs();

    await expect(editor.nameModal).toBeVisible();
    await editor.saveAsName('User pipeline copy');
    await PipelineEditor.waitForLoaded(page);
    await expect(editor.pipelineInput).toHaveValue('User pipeline copy');
    await expect(editor.monacoEditor.nth(0)).toHaveText(
      // eslint-disable-next-line max-len
      'preamble:   input_reference_genome: hg38/genomes/GRCh38-hg38annotators:- allele_score:    resource_id: hg38/scores/CADD_v1.7'
    );
  });

  test('should not be able to delete and save public pipeline', async({ page }) => {
    const editor = new PipelineEditor(page);
    // The dropdown reverts to the default only after the (un-awaited) post-action
    // GET /api/pipelines returns; under daphne sync-view serialization that GET can
    // exceed the 5s default window (gain#150), so allow the response budget here.
    await expect(editor.pipelineInput).toHaveValue(
      'pipeline/hg38_clinical_annotation', { timeout: 30000 });
    await expect(editor.deleteButton).not.toBeVisible();
    await expect(editor.saveButton).not.toBeVisible();
  });

  test('should search pipeline from dropdown', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.pipelineInput.fill('clini');
    await expect(page.locator('mat-option')).toHaveCount(3);
    await expect(page.getByRole('option', { name: 'circle pipeline/hs1_clinical_annotation' })).toBeVisible();
    await expect(page.getByRole('option', { name: 'circle pipeline/hg38_clinical_annotation' })).toBeVisible();
    await expect(page.getByRole('option', { name: 'circle pipeline/hg19_clinical_annotation' })).toBeVisible();
  });

  test('should search for nonexistent pipeline in dropdown', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.pipelineInput.fill('piepline');
    await expect(page.locator('mat-option')).toHaveCount(0);
  });

  test('should save user pipeline with Ctrl+S', async({ page }) => {
    const editor = new PipelineEditor(page);
    await customDefaultPipeline(page);

    await editor.saveAs();
    await editor.saveAsName('My pipeline');

    await expect(editor.pipelineInput).toHaveValue('My pipeline');

    /* eslint-disable */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      const model = monaco.editor.getModels()[0];
      model.applyEdits([{ range: new monaco.Range(5, 1, 5, 1), text: '    # edited\n' }]);
    });
    /* eslint-enable */

    await expect(editor.pipelineInput).toHaveValue('My pipeline *');

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user') && resp.request().method() === 'POST'
    );
    await page.keyboard.press('Control+s');
    await saveResponse;

    await expect(editor.pipelineInput).toHaveValue('My pipeline');
  });

  test('should cancel name modal without saving pipeline', async({ page }) => {
    const editor = new PipelineEditor(page);
    await customDefaultPipeline(page);

    await editor.saveAs();
    await expect(editor.nameModal).toBeVisible();
    await editor.nameInput.fill('My Pipeline');
    await editor.cancelNameButton.click();

    await expect(editor.nameModal).not.toBeVisible();
    await expect(editor.pipelineInput).toBeEmpty();
  });

  test('should show error when saving pipeline with already existing name', async({ page }) => {
    const editor = new PipelineEditor(page);
    await customDefaultPipeline(page);

    await editor.saveAs();
    await editor.saveAsName('My Pipeline');

    await expect(editor.pipelineInput).toHaveValue('My Pipeline');

    await editor.saveAs();
    await editor.nameInput.fill('My Pipeline');
    await editor.saveNameButton.click();

    await expect(editor.nameError).toHaveText('Pipeline with this name already exists.');
    await expect(editor.nameModal).toBeVisible();
  });

  test('should keep pipeline when Cancel is clicked on delete confirmation', async({ page }) => {
    const editor = new PipelineEditor(page);
    await customDefaultPipeline(page);
    await editor.saveAs();
    await editor.saveAsName('My Pipeline');
    await expect(editor.pipelineInput).toHaveValue('My Pipeline');

    await editor.delete();
    await expect(editor.deleteConfirmPopover).toBeVisible();
    await editor.cancelDeleteButton.click();

    await expect(editor.deleteConfirmPopover).not.toBeVisible();
    await expect(editor.pipelineInput).toHaveValue('My Pipeline');
  });

  test('should save pipeline name via Enter key in name modal', async({ page }) => {
    const editor = new PipelineEditor(page);
    await customDefaultPipeline(page);
    await editor.saveAs();
    await expect(editor.nameModal).toBeVisible();
    await editor.nameInput.fill('My Pipeline');

    await Promise.all([
      editor.nameInput.press('Enter'),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/load')),
    ]);

    await expect(editor.nameModal).not.toBeVisible();
    await expect(editor.pipelineInput).toHaveValue('My Pipeline');
  });

  test('should disable Save button when pipeline is unchanged and enable it after an edit', async({ page }) => {
    const editor = new PipelineEditor(page);
    await customDefaultPipeline(page);
    await editor.saveAs();
    await editor.saveAsName('My Pipeline');

    await expect(editor.saveButton).toBeDisabled();

    /* eslint-disable */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      const model = monaco.editor.getModels()[0];
      model.applyEdits([{ range: new monaco.Range(5, 1, 5, 1), text: '    # edited\n' }]);
    });
    /* eslint-enable */

    await expect(editor.saveButton).toBeEnabled();
  });

  test('should show Public pipelines and User pipelines group labels in dropdown', async({ page }) => {
    const editor = new PipelineEditor(page);
    await customDefaultPipeline(page);
    await editor.saveAs();
    await editor.saveAsName('My Pipeline');
    await expect(editor.pipelineInput).toHaveValue('My Pipeline');
    await editor.pipelineInput.click();

    await expect(page.getByRole('group', { name: 'Public pipelines' })).toBeVisible();
    await expect(page.getByRole('group', { name: 'User pipelines' })).toBeVisible();
  });
});

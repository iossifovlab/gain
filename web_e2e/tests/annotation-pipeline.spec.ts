import { test, expect, Page } from '@playwright/test';
import * as utils from '../utils';

test.describe('Pipeline tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should create new pipeline and save it', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await expect(page.locator('#pipelines-input')).toBeEmpty();
    await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

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

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.getByRole('button', { name: 'Save as' }).click();

    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('My Pipeline');
    await page.locator('#name-modal').getByRole('button', { name: 'Save' }).click();

    await expect(page.locator('#pipelines-input')).toHaveValue('My Pipeline');
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });


  test('should create new pipeline and use it without saving it', async({ page }) => {
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline' }).click();

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

    await page.locator('#examples-button').click();
    await page.getByRole('menuitem', {name: 'chr1 11796321 G A', exact: true}).click();
    await expect(page.locator('#pipelines-input')).toBeEmpty();
    await expect(page.locator('#report')).toBeVisible({timeout: 120000});
  });

  test('should not be able to save pipeline if invalid', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
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
    await expect(page.getByRole('button', { name: 'Save as' })).toBeDisabled();
  });

  test('should edit public pipeline and annotate with it', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    /* eslint-disable */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      const model = monaco.editor.getModels()[0];

      model.applyEdits([
        {
          range: new monaco.Range(19, 1, 88, 1), // clear from line 19 col 1 to line 88 col 1
          text: ''
        }
      ]);
    });
    /* eslint-enable */

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await expect(page.locator('#pipelines-input')).toBeEmpty();

    await page.locator('#examples-button').click();
    await page.getByRole('menuitem', {name: 'chr1 11796321 G A', exact: true}).click();
    await expect(page.locator('#report')).toBeVisible({timeout: 120000});
  });

  test('should edit user pipeline and save it', async({ page }) => {
    // create pipeline
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await expect(page.locator('#pipelines-input')).toBeEmpty();
    await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

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

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.getByRole('button', { name: 'Save as' }).click();

    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('My pipeline');

    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/load') // wait for pipeline to be saved and loaded
      ),
    ]);

    await expect(page.locator('#pipelines-input')).toHaveValue('My pipeline');

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

    await expect(page.locator('#pipelines-input')).toHaveValue('My pipeline *');
    await page.locator('#save-button').click();
    await expect(page.locator('#pipelines-input')).toHaveValue('My pipeline');
  });

  test('should delete user pipeline', async({ page }) => {
    // create pipeline
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await expect(page.locator('#pipelines-input')).toBeEmpty();
    await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

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

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.getByRole('button', { name: 'Save as' }).click();

    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('My pipeline');

    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/load') // wait for pipeline to be saved and loaded
      ),
    ]);

    await page.getByRole('button', { name: 'Delete' }).click();

    await Promise.all([
      page.locator('#confirm-delete').click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
      ),
    ]);
    // The dropdown reverts to the default only after the (un-awaited) post-action
    // GET /api/pipelines returns; under daphne sync-view serialization that GET can
    // exceed the 5s default window (gain#150), so allow the response budget here.
    await expect(page.locator('#pipelines-input')).toHaveValue(
      'pipeline/hg38_clinical_annotation', { timeout: 30000 });
  });

  test('should make copy of public pipeline by clicking \'save as\'', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await page.getByRole('button', { name: 'Save as' }).click();

    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('Public pipeline copy');

    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/load'), {timeout: 30000 } // wait for pipeline to be saved and loaded
      ),
    ]);

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await expect(page.locator('#pipelines-input')).toHaveValue('Public pipeline copy', { timeout: 30000 });
  });

  test('should make copy of user pipeline by clicking \'save as\'', async({ page }) => {
    // create pipeline
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await expect(page.locator('#pipelines-input')).toBeEmpty();
    await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

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

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.getByRole('button', { name: 'Save as' }).click();

    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('My pipeline');

    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/load') // wait for pipeline to be saved and loaded
      ),
    ]);

    await page.getByRole('button', { name: 'Save as' }).click();

    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('User pipeline copy');

    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/load') // wait for pipeline to be saved and loaded
      ),
    ]);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await expect(page.locator('#pipelines-input')).toHaveValue('User pipeline copy');
    await expect(page.locator('.monaco-editor').nth(0)).toHaveText(
      // eslint-disable-next-line max-len
      'preamble:   input_reference_genome: hg38/genomes/GRCh38-hg38annotators:- allele_score:    resource_id: hg38/scores/CADD_v1.7'
    );
  });

  test('should not be able to delete and save public pipeline', async({ page }) => {
    // The dropdown reverts to the default only after the (un-awaited) post-action
    // GET /api/pipelines returns; under daphne sync-view serialization that GET can
    // exceed the 5s default window (gain#150), so allow the response budget here.
    await expect(page.locator('#pipelines-input')).toHaveValue(
      'pipeline/hg38_clinical_annotation', { timeout: 30000 });
    await expect(page.getByRole('button', { name: 'Delete' })).not.toBeVisible();
    await expect(page.getByRole('button', { name: 'Save', exact: true })).not.toBeVisible();
  });

  test('should search pipeline from dropdown', async({ page }) => {
    await page.locator('#pipelines-input').fill('clini');
    await expect(page.locator('mat-option')).toHaveCount(3);
    await expect(page.getByRole('option', { name: 'circle pipeline/T2T_clinical_annotation' })).toBeVisible();
    await expect(page.getByRole('option', { name: 'circle pipeline/hg38_clinical_annotation' })).toBeVisible();
    await expect(page.getByRole('option', { name: 'circle pipeline/hg19_clinical_annotation' })).toBeVisible();
  });

  test('should search for nonexistent pipeline in dropdown', async({ page }) => {
    await page.locator('#pipelines-input').fill('piepline');
    await expect(page.locator('mat-option')).toHaveCount(0);
  });

  test('should save user pipeline with Ctrl+S', async({ page }) => {
    await customDefaultPipeline(page);

    await page.getByRole('button', { name: 'Save as' }).click();
    await page.locator('#name-modal input').fill('My pipeline');
    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/load')),
    ]);

    await expect(page.locator('#pipelines-input')).toHaveValue('My pipeline');

    /* eslint-disable */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      const model = monaco.editor.getModels()[0];
      model.applyEdits([{ range: new monaco.Range(5, 1, 5, 1), text: '    # edited\n' }]);
    });
    /* eslint-enable */

    await expect(page.locator('#pipelines-input')).toHaveValue('My pipeline *');

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user') && resp.request().method() === 'POST'
    );
    await page.keyboard.press('Control+s');
    await saveResponse;

    await expect(page.locator('#pipelines-input')).toHaveValue('My pipeline');
  });

  test('should cancel name modal without saving pipeline', async({ page }) => {
    await customDefaultPipeline(page);

    await page.getByRole('button', { name: 'Save as' }).click();
    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('My Pipeline');
    await page.locator('#cancel-button').click();

    await expect(page.locator('#name-modal')).not.toBeVisible();
    await expect(page.locator('#pipelines-input')).toBeEmpty();
  });

  test('should show error when saving pipeline with already existing name', async({ page }) => {
    await customDefaultPipeline(page);

    await page.getByRole('button', { name: 'Save as' }).click();
    await page.locator('#name-modal input').fill('My Pipeline');
    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/load')),
    ]);

    await expect(page.locator('#pipelines-input')).toHaveValue('My Pipeline');

    await page.getByRole('button', { name: 'Save as' }).click();
    await page.locator('#name-modal input').fill('My Pipeline');
    await page.locator('#name-modal').getByRole('button', { name: 'Save' }).click();

    await expect(page.locator('#name-modal .error-message')).toHaveText('Pipeline with this name already exists.');
    await expect(page.locator('#name-modal')).toBeVisible();
  });

  test('should keep pipeline when Cancel is clicked on delete confirmation', async({ page }) => {
    await customDefaultPipeline(page);
    await page.getByRole('button', { name: 'Save as' }).click();
    await page.locator('#name-modal input').fill('My Pipeline');
    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/load')),
    ]);
    await expect(page.locator('#pipelines-input')).toHaveValue('My Pipeline');

    await page.getByRole('button', { name: 'Delete' }).click();
    await expect(page.locator('#delete-confirmation-popover')).toBeVisible();
    await page.locator('#cancel-delete').click();

    await expect(page.locator('#delete-confirmation-popover')).not.toBeVisible();
    await expect(page.locator('#pipelines-input')).toHaveValue('My Pipeline');
  });

  test('should save pipeline name via Enter key in name modal', async({ page }) => {
    await customDefaultPipeline(page);
    await page.getByRole('button', { name: 'Save as' }).click();
    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('My Pipeline');

    await Promise.all([
      page.locator('#name-modal input').press('Enter'),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/load')),
    ]);

    await expect(page.locator('#name-modal')).not.toBeVisible();
    await expect(page.locator('#pipelines-input')).toHaveValue('My Pipeline');
  });

  test('should disable Save button when pipeline is unchanged and enable it after an edit', async({ page }) => {
    await customDefaultPipeline(page);
    await page.getByRole('button', { name: 'Save as' }).click();
    await page.locator('#name-modal input').fill('My Pipeline');
    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/load')),
    ]);

    await expect(page.locator('#save-button')).toBeDisabled();

    /* eslint-disable */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      const model = monaco.editor.getModels()[0];
      model.applyEdits([{ range: new monaco.Range(5, 1, 5, 1), text: '    # edited\n' }]);
    });
    /* eslint-enable */

    await expect(page.locator('#save-button')).toBeEnabled();
  });

  test('should show Public pipelines and User pipelines group labels in dropdown', async({ page }) => {
    await customDefaultPipeline(page);
    await page.getByRole('button', { name: 'Save as' }).click();
    await page.locator('#name-modal input').fill('My Pipeline');
    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/load')),
    ]);
    await expect(page.locator('#pipelines-input')).toHaveValue('My Pipeline');
    await page.locator('#pipelines-input').click();

    await expect(page.getByRole('group', { name: 'Public pipelines' })).toBeVisible();
    await expect(page.getByRole('group', { name: 'User pipelines' })).toBeVisible();
  });
});


test.describe('Pipeline validation tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should type config without annotators and show error message', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await utils.typeInPipelineEditor(page, 'preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });
    await expect(page.getByText('Invalid configuration, reason: \'annotators\'')).toBeVisible();
  });

  test('should type config without peamble and show error message', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await utils.typeInPipelineEditor(page, 'annotators:\n - allele_score: hg38/scores/CADD_v1.7');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });
    await expect(page.getByText('Invalid configuration, reason: \'preamble\'')).toBeVisible();
  });

  test('should type semantically invalid config and display error', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await utils.typeInPipelineEditor(page, '- allele_score');

    await expect(page.locator('.error-message').nth(0)).toContainText(
      'Invalid configuration, reason: The A0 annotator configuration is incorrect:  ' +
      'The AnnotatorInfo(annotator_id=\'A0\', type=\'allele_score\', attributes=[], ' +
      'parameters={\'work_dir\': \'work/A0_allele_score\'}, documentation=\'\', resources=[]) ' +
      'has not \'resource_id\' parameters');
  });
});

test.describe('Pipeline confirmation popup tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  const pipelineContent =
    'preamble:\n' +
    '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
    'annotators:\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/scores/CADD_v1.7\n';

  async function setupTempPipeline(page: Page): Promise<void> {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );
    await utils.typeInPipelineEditor(page, pipelineContent);
    await saveResponse;
  }

  test('should show confirmation popup when selecting a pipeline with unsaved temp changes', async({ page }) => {
    await setupTempPipeline(page);

    await page.locator('.dropdown-icon').click();

    await expect(page.locator('#change-confirmation-popover')).toBeVisible();
    await expect(page.locator('#change-confirmation-popover p')).toHaveText(
      'Are you sure? You are going to lose your changes.'
    );
    await expect(page.locator('#confirm-change')).toBeVisible();
    await expect(page.locator('#cancel-change')).toBeVisible();
  });

  test('should open pipeline dropdown after confirming pipeline selection', async({ page }) => {
    await setupTempPipeline(page);

    await page.locator('.dropdown-icon').click();
    await expect(page.locator('#change-confirmation-popover')).toBeVisible();

    await page.locator('#confirm-change').click();

    await expect(page.locator('#change-confirmation-popover')).not.toBeVisible();
    await expect(page.locator('#pipelines-input')).toBeEmpty();
    await expect(page.locator('mat-option').first()).toBeVisible();
  });

  test('should keep unsaved changes after cancelling pipeline selection', async({ page }) => {
    await setupTempPipeline(page);

    await page.locator('.dropdown-icon').click();
    await expect(page.locator('#change-confirmation-popover')).toBeVisible();

    await page.locator('#cancel-change').click();

    await expect(page.locator('#change-confirmation-popover')).not.toBeVisible();
    await expect(page.locator('.monaco-editor').nth(0)).toHaveText(
      'preamble:' +
      ' input_reference_genome: hg38/genomes/GRCh38-hg38' +
      'annotators:' +
      '- allele_score:' +
      '   resource_id: hg38/scores/CADD_v1.7');
  });

  test('should show confirmation popup when clicking "New pipeline" with unsaved temp changes', async({ page }) => {
    await setupTempPipeline(page);

    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();

    await expect(page.locator('#create-confirmation-popover')).toBeVisible();
    await expect(page.locator('#create-confirmation-popover p')).toHaveText(
      'Are you sure? You are going to lose your changes.'
    );
    await expect(page.locator('#confirm-change')).toBeVisible();
    await expect(page.locator('#cancel-change')).toBeVisible();
  });

  test('should clear pipeline after confirming new pipeline creation', async({ page }) => {
    await setupTempPipeline(page);

    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await expect(page.locator('#create-confirmation-popover')).toBeVisible();

    await page.locator('#confirm-change').click();

    await expect(page.locator('#create-confirmation-popover')).not.toBeVisible();
    await expect(page.locator('#pipelines-input')).toBeEmpty();
    await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();
  });

  test('should keep unsaved changes after cancelling new pipeline creation', async({ page }) => {
    await setupTempPipeline(page);

    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await expect(page.locator('#create-confirmation-popover')).toBeVisible();

    await page.locator('#cancel-change').click();

    await expect(page.locator('#create-confirmation-popover')).not.toBeVisible();
    await expect(page.locator('.monaco-editor').nth(0)).not.toBeEmpty();
  });

  test('should show confirmation popup when selecting pipeline with unsaved user pipeline changes', async({ page }) => {
    // create and save a user pipeline first
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );
    await utils.typeInPipelineEditor(page, pipelineContent);
    await saveResponse;

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await page.getByRole('button', { name: 'Save as' }).click();
    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('My Pipeline');

    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/load')),
    ]);

    await expect(page.locator('#pipelines-input')).toHaveValue('My Pipeline');

    // modify the saved pipeline to trigger unsaved indicator (*)
    /* eslint-disable */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      const model = monaco.editor.getModels()[0];
      model.applyEdits([{
        range: new monaco.Range(5, 1, 5, 1),
        text: '    # edited\n'
      }]);
    });
    /* eslint-enable */

    await expect(page.locator('#pipelines-input')).toHaveValue('My Pipeline *');

    await page.locator('.dropdown-icon').click();

    await expect(page.locator('#change-confirmation-popover')).toBeVisible();
    await expect(page.locator('#change-confirmation-popover p')).toHaveText(
      'Are you sure? You are going to lose your changes.'
    );
  });
});

test.describe('Add annotator to pipeline tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000});
  });

  test('should open new annotator dialog with correct header and first step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await expect(page.locator('mat-dialog-container')).toBeVisible();
    await expect(page.locator('#modal-header')).toHaveText('New annotator');
    await expect(page.getByRole('combobox', { name: 'Select annotator' })).toBeVisible();
  });

  test('should append gene set annotator', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('gene_set_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="resource_id-dropdown"]').click();
    await page.locator('mat-option').getByText('gene_properties/gene_sets/autism').click();
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
      '    resource_id: gene_properties/gene_sets/autism\n' +
      '    input_gene_list: gene_list\n'+
      '    attributes:\n'+
      '    - name: in_sets\n'+
      '      source: in_sets\n' +
      '      internal: false\n'
    );
  });

  test('should append two annotators', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('liftover_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="chain-dropdown"]').click();
    await page.locator('mat-option').getByText('liftover/hg19_to_T2T').click();

    await page.locator('[id="source_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('t2t/genomes/t2t-chm13v2.0').click();

    await page.locator('[id="target_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/genomes/GRCh38.p14').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/validate')
      ),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/user'), // wait for pipeline to be saved
        {timeout: 20000}, // hg38_to_t2t chain loading can take a while, increase timeout for this test
      ),
    ]);

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('position_score_annotator').click();
    await page.waitForTimeout(3000);
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="resource_id-dropdown"]').click();
    await page.locator('mat-option').getByText('hg19/scores/FitCons2/E050').click();

    await page.getByRole('button', { name: 'Next' }).click();

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
      '- liftover_annotator:\n' +
      '    chain: liftover/hg19_to_T2T\n' +
      '    source_genome: t2t/genomes/t2t-chm13v2.0\n' +
      '    target_genome: hg38/genomes/GRCh38.p14\n' +
      '    attributes:\n' +
      '    - name: liftover_annotatable\n' +
      '      source: liftover_annotatable\n' +
      '      internal: true\n' +
      '\n' +
      '- position_score_annotator:\n' +
      '    resource_id: hg19/scores/FitCons2/E050\n' +
      '    attributes:\n' +
      '    - name: FitCons2_E050\n' +
      '      source: FitCons2_E050\n' +
      '      internal: false'
    );
  });

  test('should append annotator to user pipeline', async({ page }) => {
    await customDefaultPipeline(page);

    await page.getByRole('button', { name: 'Save as' }).click();

    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('My Pipeline');

    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/load') // wait for pipeline to be saved and loaded
      ),
      page.waitForResponse(
        resp => resp.url().includes('api/editor/pipeline_status?pipeline_id'),
        {timeout: 300000}
      )
    ]);

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    // append new annotator
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();

    await page.getByRole('button', { name: 'Next' }).click();

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
      'preamble:\n' +
      '   input_reference_genome: hg38/genomes/GRCh38-hg38' +
      '\n' +
      'annotators:\n' +
      '- allele_score:\n' +
      '    resource_id: hg38/scores/CADD_v1.7\n' +
      '\n' +
      '- simple_effect_annotator:\n' +
      '    gene_models: hg38/gene_models/GENCODE/46/basic/PRI\n' +
      '    attributes:\n' +
      '    - name: worst_effect\n' +
      '      source: worst_effect\n' +
      '      internal: false\n' +
      '    - name: worst_effect_genes\n' +
      '      source: worst_effect_genes\n' +
      '      internal: false\n' +
      '    - name: gene_list\n' +
      '      source: gene_list\n' +
      '      internal: true\n'
    );
  });

  test('should disable Next button when no annotator is selected and enable it after selection', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('allele_score').click();

    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  test('should filter annotators in dropdown by search text', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).fill('allele');
    await page.getByRole('combobox', { name: 'allele' }).dispatchEvent('input');

    await expect(page.locator('.annotator-option')).toHaveCount(2);
    await expect(page.locator('.annotator-option').filter({ hasText: 'allele_score_annotator' })).toBeVisible();
    await expect(page.locator('.annotator-option').filter({ hasText: 'normalize_allele_annotator' })).toBeVisible();
  });

  test('should navigate back from configure step to annotator selection step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('allele_score').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('[id="resource_id-dropdown"]')).toBeVisible();

    await page.getByRole('button', { name: 'Back' }).click();

    await expect(page.getByRole('combobox', { name: 'allele_score_annotator' })).toBeVisible();
  });

  test('should check selected data in summary panel', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('allele_score').click();
    await page.getByRole('button', { name: 'Next' }).click();

    // configure step: summary shows the selected annotator
    const configureSummary = page.locator('.mat-horizontal-stepper-content-current');
    await expect(configureSummary.locator('.annotator-display-text')).toContainText('annotator');
    await expect(configureSummary.locator('.annotator-display-text')).toContainText('allele_score_annotator');

    await page.locator('[id="resource_id-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/scores/CADD_v1.7').click();
    await page.getByRole('button', { name: 'Next' }).click();

    // attribute step: summary adds the configured resource_id beneath the annotator
    const attributeSummary = page.locator('.mat-horizontal-stepper-content-current');
    await expect(attributeSummary.locator('.annotator-display-text')).toContainText('allele_score_annotator');
    const resourceIdDisplay = attributeSummary.locator('.resources-display-text').filter({ hasText: 'resource_id' });
    await expect(resourceIdDisplay).toContainText('hg38/scores/CADD_v1.7');
  });

  test('should remove a default attribute in the attribute step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('.attribute-source')).toHaveCount(3);

    await page.locator('#gene_list-remove-button').click();

    await expect(page.locator('.attribute-source')).toHaveCount(2);
  });

  test('should rename attribute and reflect new name in finished YAML', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('.editable-name').first().fill('my_worst_effect');
    await page.locator('.editable-name').first().blur();

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/validate')),
    ]);

    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const value = await page.evaluate(() => {
      // eslint-disable-next-line max-len
      // eslint-disable-next-line @typescript-eslint/no-unsafe-return, @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-unsafe-call, @typescript-eslint/no-explicit-any
      return (window as any).monaco.editor.getModels()[0].getValue();
    });

    expect(value).toContain('name: my_worst_effect');
    expect(value).not.toContain('name: worst_effect\n');
  });

  test('should show duplicate attribute name error and disable Finish button', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('.attribute-source')).toHaveCount(3);

    await page.locator('.editable-name').first().fill('worst_effect_genes');
    await page.locator('.editable-name').first().blur();

    await expect(page.locator('.error-message')).toContainText('Attribute with this name already exists');
    await expect(page.getByRole('button', { name: 'Finish' })).toBeDisabled();
  });

  test('should disable New annotator button when pipeline config is invalid', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await utils.typeInPipelineEditor(page, 'preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });

    await expect(page.locator('#pipeline-actions').locator('#add-annotator-button')).toBeDisabled();
  });

  test('should enable Next button on configure step only after all required fields are filled', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('liftover_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="chain-dropdown"]').click();
    await page.locator('mat-option').getByText('liftover/hg19_to_T2T').click();
    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();

    await page.locator('[id="source_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('t2t/genomes/t2t-chm13v2.0').click();
    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();

    await page.locator('[id="target_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/genomes/GRCh38.p14').click();
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  test('should enable Next when only required field is filled', async({ page }) => {
    // effect_annotator has gene_models (required) and genome (optional resource)
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('effect_annotator', { exact: true }).click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('[id="gene_models-dropdown"]')).toBeVisible();
    await expect(page.locator('[id="genome-dropdown"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();

    // Next is enabled without filling optional genome field
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  test('should keep Next enabled after filling an optional resource field', async({ page }) => {
    // effect_annotator: fill required gene_models then also fill optional genome
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('effect_annotator', { exact: true }).click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();

    await page.locator('[id="genome-dropdown"]').click();
    await page.locator('mat-option.resource-option').first().click();
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  test('should filter chain options by search text in configure step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('liftover_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="chain-dropdown"]').locator('.dropdown-icon').click();

    await page.locator('[id="chain-dropdown"] input').pressSequentially('hg19');
    const filteredOptions = page.locator('mat-option.resource-option');

    expect(await filteredOptions.count()).toBe(4);
  });

  test('should filter source_genome options by search text in configure step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('liftover_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="source_genome-dropdown"]').locator('.dropdown-icon').click();

    await page.locator('[id="source_genome-dropdown"] input').pressSequentially('t2t');
    const filteredOptions = page.locator('mat-option.resource-option');

    expect(await filteredOptions.count()).toBe(1);
  });

  test('should disable Next button in configure step when a filled required field is cleared', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('liftover_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="chain-dropdown"]').click();
    await page.locator('mat-option').getByText('liftover/hg19_to_T2T').click();
    await page.locator('[id="source_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('t2t/genomes/t2t-chm13v2.0').click();
    await page.locator('[id="target_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/genomes/GRCh38.p14').click();

    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();

    // clicking the dropdown icon clears the field and opens the panel
    await page.locator('[id="chain-dropdown"]').locator('.dropdown-icon').click();
    await page.keyboard.press('Escape');

    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();
  });

  test('should toggle attribute internal flag and reflect it in finished YAML', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    // gene_list is the 3rd attribute (index 2) and internal: true by default
    const geneListCheckbox = page.locator('.attribute-internal input[type="checkbox"]').nth(2);
    await expect(geneListCheckbox).toBeChecked();
    await geneListCheckbox.click();
    await expect(geneListCheckbox).not.toBeChecked();

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/validate')),
    ]);

    /* eslint-disable */
    const value = await page.evaluate(() => {
      return (window as any).monaco.editor.getModels()[0].getValue();
    });
    /* eslint-enable */

    expect(value).toContain('name: gene_list\n      source: gene_list\n      internal: false');
  });

  // eslint-disable-next-line max-len
  test('should select all attributes, remove all, then select one from dropdown as the only selected', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('.attribute-source')).toHaveCount(3);

    await page.getByRole('button', { name: 'Select all' }).click();
    await expect(page.locator('.attributes-section-label')).toContainText('(15)');

    await page.getByRole('button', { name: 'Remove all' }).click();
    await expect(page.locator('.attributes-section-label')).not.toBeVisible();

    await page.locator('#attributes-dropdown .dropdown-icon').click();
    await page.locator('mat-option.attribute-option').first().click();

    await expect(page.locator('.attribute-source')).toHaveCount(1);
    await expect(page.locator('.attribute-source').first()).toHaveText('worst_effect');
  });
});


test.describe('Add resource to pipeline tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should open New resource dialog with correct header and first step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await expect(page.locator('mat-dialog-container')).toBeVisible();
    await expect(page.locator('#modal-header')).toHaveText('New resource');
    await expect(page.locator('#resource-type')).toBeVisible();
    await expect(page.locator('#resource-search-input')).toBeVisible();
    await expect(page.locator('#resource-count')).toBeVisible();
  });

  test('should display matching resources after typing in search input', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('CADD'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=CADD'), {timeout: 30000}
      )
    ]);

    await expect(page.locator('#resource-count')).toHaveText('2 resources');
    await expect(page.getByTitle('hg38/scores/CADD_v1.7')).toBeVisible();
    await expect(page.getByTitle('hg38/scores/dbNSFP4.9a')).toBeVisible();
  });

  test('should filter resources by resource type', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();
    await expect(page.locator('#resource-count')).toHaveText('259 resources');
    await page.locator('#resource-type mat-select').click();
    await page.locator('mat-option').filter({ hasText: 'gene_score' }).click();
    await expect(page.locator('#resource-count')).toHaveText('10 resources');
  });

  test('should navigate past select annotator step after clicking continue', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('"CADD_v1.7"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22CADD_v1.7%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector('[id="hg38/scores/CADD_v1.7-continue-button"]', { state: 'visible', timeout: 15000 });
    await page.locator('[id$="-continue-button"]').first().click();

    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });
    await expect(
      page.locator('.mat-horizontal-stepper-content-current').locator('.annotator-display-text')
    ).toContainText('allele_score_annotator');
  });

  test('should navigate back to previous step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('"CADD_v1.7"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22CADD_v1.7%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector('[id="hg38/scores/CADD_v1.7-continue-button"]', { state: 'visible', timeout: 15000 });
    await page.locator('[id$="-continue-button"]').first().click();

    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });

    await page.getByRole('button', { name: 'Back' }).click();

    await expect(page.locator('#annotator-input-form')).toBeVisible();
  });

  test('should check selected data in summary panel', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await page.locator('#resource-search-input').fill('"gene_properties/gene_scores/GTEx_V11_RNAexpression"');
    await Promise.all([
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search query
      page.waitForResponse(
        resp => resp.url().includes(
          'api/resources/search?search=%22gene_properties/gene_scores/GTEx_V11_RNAexpression%22'
        ), {timeout: 30000}
      )
    ]);
    await page.waitForSelector(
      '[id="gene_properties/gene_scores/GTEx_V11_RNAexpression-continue-button"]',
      { state: 'visible', timeout: 15000 }
    );
    await page.locator('[id="gene_properties/gene_scores/GTEx_V11_RNAexpression-continue-button"]').click();

    const summary = page.locator('.mat-horizontal-stepper-content-current');
    // configure annotator step
    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });
    await expect(summary.locator('.resource-type-display-text').nth(0))
      .toHaveText('resource type gene_score');
    await expect(summary.locator('.resource-type-display-text').nth(1))
      .toHaveText('resource id gene_properties/gene_scores/GTEx_V11_RNAexpression');
    await expect(summary.locator('.annotator-display-text')).toHaveText('annotatorgene_score_annotator');

    // advance to attribute step
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
    await page.getByRole('button', { name: 'Next' }).click();

    // // attribute step
    await expect(summary.locator('.resource-type-display-text').nth(0))
      .toHaveText('resource type gene_score');
    await expect(summary.locator('.resource-type-display-text').nth(1))
      .toHaveText('resource id gene_properties/gene_scores/GTEx_V11_RNAexpression');
    await expect(summary.locator('.annotator-display-text')).toHaveText('annotatorgene_score_annotator');
    await expect(summary.locator('.resources-display-text')).toHaveText('input_gene_list\ngene_list');
  });

  test('should complete workflow via finish with defaults and append YAML to editor', async({ page }) => {
    await customDefaultPipeline(page);

    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('"hg19/scores/AlphaMissense"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22hg19/scores/AlphaMissense%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector('[id="hg19/scores/AlphaMissense-finish-button"]', { state: 'visible', timeout: 15000 });

    await Promise.all([
      page.locator('[id="hg19/scores/AlphaMissense-finish-button"]').click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/validate')),
    ]);

    /* eslint-disable */
    const value = await page.evaluate(() => {
      return (window as any).monaco.editor.getModels()[0].getValue();
    });
    /* eslint-enable */

    expect(value).toContain('resource_id: hg19/scores/AlphaMissense');
  });

  test('should complete full workflow via continue and append YAML to editor', async({ page }) => {
    await customDefaultPipeline(page);

    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('"hg19/scores/AlphaMissense"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22hg19/scores/AlphaMissense%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector(
      '[id="hg19/scores/AlphaMissense-continue-button"]',
      { state: 'visible', timeout: 15000 }
    );
    await page.locator('[id="hg19/scores/AlphaMissense-continue-button"]').click();

    // resource workflow auto-selects the annotator and navigates to configure step
    await expect(page.locator('#resources-form')).toBeVisible({ timeout: 15000 });
    // resource_id is pre-filled from the selected resource, so Next is enabled immediately
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.getByRole('button', { name: 'Finish' })).toBeVisible({ timeout: 10000 });

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/editor/annotator_yaml')),
    ]);

    /* eslint-disable */
    const value = await page.evaluate(() => {
      return (window as any).monaco.editor.getModels()[0].getValue();
    });
    /* eslint-enable */

    expect(value).toContain('resource_id: hg38/scores/CADD_v1.7');
  });

  test('should disable New resource button when pipeline config is invalid', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await utils.typeInPipelineEditor(page, 'preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });

    await expect(page.locator('#pipeline-actions').locator('#add-resource-button')).toBeDisabled();
  });

  test('should open resource details in new tab', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-resource-button').click();

    await Promise.all([
      page.locator('#resource-search-input').fill('"CADD_v1.7"'),
      page.locator('#resource-search-input').dispatchEvent('keyup'), // trigger search
      page.waitForResponse(
        resp => resp.url().includes('api/resources/search?search=%22CADD_v1.7%22'), {timeout: 30000}
      )
    ]);

    await page.waitForSelector(
      '[id="hg38/scores/CADD_v1.7-resource-details-button"]',
      { state: 'visible', timeout: 15000 }
    );

    const [popup] = await Promise.all([
      page.context().waitForEvent('page'),
      page.locator('[id="hg38/scores/CADD_v1.7-resource-details-button"] a').click(),
    ]);

    await popup.waitForLoadState('domcontentloaded');
    expect(popup.url()).toContain('/hg38/scores/CADD_v1.7/index.html');
  });
});


test.describe('Pipeline status bar tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('should show annotatable count in status bar', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await expect(page.locator('#pipelines-input')).toBeEmpty();
    await expect(page.locator('.monaco-editor').nth(0)).toBeEmpty();

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

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await expect(page.locator('#status-bar .status-item').nth(2)).toContainText('1 annotatables');
  });

  test('should show gene list count in status bar for pipeline with gene list attribute', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();

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
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await expect(page.locator('#status-bar .status-item').nth(3)).toContainText('1 gene list');
  });

  test('should update annotator and attribute counts in status bar after adding annotator', async({ page }) => {
    await customDefaultPipeline(page);

    await expect(page.locator('#status-bar .status-item').nth(0)).toHaveText('menu1 annotators');
    await expect(page.locator('#status-bar .status-item').nth(1)).toHaveText('menu_open2 attributes');

    // add simple_effect_annotator which contributes 3 attributes
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/editor/pipeline_status')),
    ]);

    await expect(page.locator('#status-bar .status-item').nth(0)).toHaveText('menu2 annotators');
    await expect(page.locator('#status-bar .status-item').nth(1)).toHaveText('menu_open5 attributes');
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
    'preamble:\n' +
    '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
    'annotators:\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/scores/CADD_v1.7\n'
  );

  await saveResponse;

  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}
import { test, expect } from '@playwright/test';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

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

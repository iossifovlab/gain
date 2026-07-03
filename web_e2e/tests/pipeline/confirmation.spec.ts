import { test, expect, Page } from '@playwright/test';
import * as utils from '../../utils';
import { PipelineEditor } from '../../pages/pipeline-editor.page';

test.describe('Pipeline confirmation popup tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await PipelineEditor.waitForLoaded(page);
  });

  const pipelineContent =
    'preamble:\n' +
    '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
    'annotators:\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/scores/CADD_v1.7\n';

  async function setupTempPipeline(page: Page): Promise<void> {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();
    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );
    await utils.typeInPipelineEditor(page, pipelineContent);
    await saveResponse;
  }

  test('should show confirmation popup when selecting a pipeline with unsaved temp changes', async({ page }) => {
    const editor = new PipelineEditor(page);
    await setupTempPipeline(page);

    await editor.dropdownIcon.click();

    await expect(editor.changeConfirmPopover).toBeVisible();
    await expect(editor.changeConfirmPopover.locator('p')).toHaveText(
      'Are you sure? You are going to lose your changes.'
    );
    await expect(editor.confirmChangeButton).toBeVisible();
    await expect(editor.cancelChangeButton).toBeVisible();
  });

  test('should open pipeline dropdown after confirming pipeline selection', async({ page }) => {
    const editor = new PipelineEditor(page);
    await setupTempPipeline(page);

    await editor.dropdownIcon.click();
    await expect(editor.changeConfirmPopover).toBeVisible();

    await editor.confirmChangeButton.click();

    await expect(editor.changeConfirmPopover).not.toBeVisible();
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(page.locator('mat-option').first()).toBeVisible();
  });

  test('should keep unsaved changes after cancelling pipeline selection', async({ page }) => {
    const editor = new PipelineEditor(page);
    await setupTempPipeline(page);

    await editor.dropdownIcon.click();
    await expect(editor.changeConfirmPopover).toBeVisible();

    await editor.cancelChangeButton.click();

    await expect(editor.changeConfirmPopover).not.toBeVisible();
    await expect(editor.monacoEditor.nth(0)).toHaveText(
      'preamble:' +
      ' input_reference_genome: hg38/genomes/GRCh38-hg38' +
      'annotators:' +
      '- allele_score:' +
      '   resource_id: hg38/scores/CADD_v1.7');
  });

  test('should show confirmation popup when clicking "New pipeline" with unsaved temp changes', async({ page }) => {
    const editor = new PipelineEditor(page);
    await setupTempPipeline(page);

    await editor.newPipeline();

    await expect(editor.createConfirmPopover).toBeVisible();
    await expect(editor.createConfirmPopover.locator('p')).toHaveText(
      'Are you sure? You are going to lose your changes.'
    );
    await expect(editor.confirmChangeButton).toBeVisible();
    await expect(editor.cancelChangeButton).toBeVisible();
  });

  test('should clear pipeline after confirming new pipeline creation', async({ page }) => {
    const editor = new PipelineEditor(page);
    await setupTempPipeline(page);

    await editor.newPipeline();
    await expect(editor.createConfirmPopover).toBeVisible();

    await editor.confirmChangeButton.click();

    await expect(editor.createConfirmPopover).not.toBeVisible();
    await expect(editor.pipelineInput).toBeEmpty();
    await expect(editor.monacoEditor.nth(0)).toBeEmpty();
  });

  test('should keep unsaved changes after cancelling new pipeline creation', async({ page }) => {
    const editor = new PipelineEditor(page);
    await setupTempPipeline(page);

    await editor.newPipeline();
    await expect(editor.createConfirmPopover).toBeVisible();

    await editor.cancelChangeButton.click();

    await expect(editor.createConfirmPopover).not.toBeVisible();
    await expect(editor.monacoEditor.nth(0)).not.toBeEmpty();
  });

  test('should show confirmation popup when selecting pipeline with unsaved user pipeline changes', async({ page }) => {
    const editor = new PipelineEditor(page);
    // create and save a user pipeline first
    await editor.newPipeline();

    const saveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
    );
    await utils.typeInPipelineEditor(page, pipelineContent);
    await saveResponse;

    await PipelineEditor.waitForLoaded(page);
    await editor.saveAs();
    await expect(editor.nameModal).toBeVisible();
    await editor.saveAsName('My Pipeline');

    await expect(editor.pipelineInput).toHaveValue('My Pipeline');

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

    await expect(editor.pipelineInput).toHaveValue('My Pipeline *');

    await editor.dropdownIcon.click();

    await expect(editor.changeConfirmPopover).toBeVisible();
    await expect(editor.changeConfirmPopover.locator('p')).toHaveText(
      'Are you sure? You are going to lose your changes.'
    );
  });
});

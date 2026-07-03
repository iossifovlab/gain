import { test, expect, Page } from '@playwright/test';
import * as utils from '../../utils';

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

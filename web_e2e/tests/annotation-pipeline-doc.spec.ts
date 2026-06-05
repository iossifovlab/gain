import * as fs from 'fs';
import { test, expect, Page } from '@playwright/test';
import * as utils from '../utils';
const VALID_PIPELINE_DOC =
  'preamble:\n' +
  '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
  'annotators:\n' +
  '- allele_score:\n' +
  '    resource_id: hg38/scores/CADD_v1.7\n';

async function downloadDoc(page: Page): Promise<{ suggestedFilename: string; content: string }> {
  const downloadPromise = page.waitForEvent('download');
  await page.locator('#download-pipeline-documentation').click();
  const download = await downloadPromise;
  const suggestedFilename = download.suggestedFilename();
  expect(suggestedFilename).toMatch(/\.html$/);
  const filePath = await download.path();
  const content = fs.readFileSync(filePath, 'utf-8');
  expect(content).toContain('Pipeline Documentation');
  return { suggestedFilename, content };
}

async function createTempPipelineDoc(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', {
    name: 'draft New pipeline', exact: true
  }).click();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), { timeout: 30000 }
  );
  await utils.typeInPipelineEditor(page, VALID_PIPELINE_DOC);
  await saveResponse;
  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

test.describe('Pipeline documentation download', () => {
  test('logged-in user can download documentation for default pipeline', async({ page }) => {
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await expect(page.locator('#download-pipeline-documentation')).toBeVisible();
    const { content } = await downloadDoc(page);
    expect(content).toMatch(/Annotator type:/);
  });

  test('logged-in user can download documentation for saved user pipeline', async({ page }) => {
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await createTempPipelineDoc(page);
    await page.getByRole('button', { name: 'Save as' }).click();
    await page.locator('#name-modal input').fill('doc-test-pipeline');
    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/user') && resp.request().method() === 'POST'
      ),
    ]);
    await expect(page.locator('#pipelines-input')).toHaveValue('doc-test-pipeline', { timeout: 30000 });
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await expect(page.locator('#download-pipeline-documentation')).toBeVisible();
    const { suggestedFilename, content } = await downloadDoc(page);
    expect(suggestedFilename).not.toContain('doc-test-pipeline');
    expect((content.match(/Annotator type:/g) ?? []).length).toBe(1);
    expect(content).toContain('Annotator type: allele_score');
    expect(content).toContain('CADD_v1.7');
  });

  test('anonymous user cannot download documentation', async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await expect(page.locator('#download-pipeline-documentation')).not.toBeVisible();
    await createTempPipelineDoc(page);
    await expect(page.locator('#download-pipeline-documentation')).not.toBeVisible();
  });

  test('logged-in user can download documentation for unsaved edits on a named pipeline', async({ page }) => {
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    // Create and save a user pipeline.
    await createTempPipelineDoc(page);
    await page.getByRole('button', { name: 'Save as' }).click();
    await page.locator('#name-modal input').fill('edit-test-pipeline');
    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/user') && resp.request().method() === 'POST'
      ),
    ]);
    await expect(page.locator('#pipelines-input')).toHaveValue('edit-test-pipeline', { timeout: 30000 });
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    // Edit the saved pipeline — adds * and triggers autosave to a temp pipeline.
    const autoSaveResponse = page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user'), { timeout: 30000 }
    );
    /* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-member-access,
                      @typescript-eslint/no-unsafe-call */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      monaco.editor.getModels()[0].applyEdits([
        { range: new monaco.Range(1, 1, 1, 1), text: '# edited\n' }
      ]);
    });
    /* eslint-enable */
    await autoSaveResponse;
    await expect(page.locator('#pipelines-input')).toHaveValue('edit-test-pipeline *', { timeout: 30000 });
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await expect(page.locator('#download-pipeline-documentation')).toBeVisible();
    const { content } = await downloadDoc(page);
    expect((content.match(/Annotator type:/g) ?? []).length).toBe(1);
    expect(content).toContain('Annotator type: allele_score');
    expect(content).toContain('CADD_v1.7');
  });

  test('download link is hidden when no pipeline is selected', async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.locator('#pipeline-actions').getByRole('button', {
      name: 'draft New pipeline', exact: true
    }).click();

    await expect(page.locator('#download-pipeline-documentation')).not.toBeVisible();
  });
});

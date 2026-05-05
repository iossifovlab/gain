import { test, expect, Page } from '@playwright/test';
import * as utils from '../utils';

const VALID_PIPELINE =
  'preamble:\n' +
  '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
  'annotators:\n' +
  '- allele_score:\n' +
  '    resource_id: hg38/scores/CADD_v1.7\n';

async function goToAnnotationJobs(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'Annotation Jobs' }).click();
  await page.waitForSelector('app-annotation-jobs-wrapper', { timeout: 30000 });
}

async function goToSingleAnnotation(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'Single Annotation' }).click();
  await page.waitForSelector('app-single-annotation-wrapper', { timeout: 30000 });
}

async function createTempPipeline(page: Page): Promise<void> {
  await page.locator('#pipeline-actions').getByRole('button', {
    name: 'draft New pipeline', exact: true
  }).click();

  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), { timeout: 30000 }
  );
  await utils.typeInPipelineEditor(page, VALID_PIPELINE);
  await saveResponse;
  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

async function createAndSaveUserPipeline(page: Page, name: string): Promise<void> {
  await createTempPipeline(page);

  await page.getByRole('button', { name: 'Save as' }).click();
  await expect(page.locator('#name-modal')).toBeVisible();
  await page.locator('#name-modal input').fill(name);

  await Promise.all([
    page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
    page.waitForResponse(resp => resp.url().includes('api/pipelines/load')),
  ]);

  await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
}

test.describe('Annotation pipeline state persistence across navigation', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  test('selected pipeline is restored on Annotation Jobs after navigating from Single Annotation', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');
    await expect(page.locator('#pipelines-input')).toHaveValue('pipeline/Clinical_annotation');

    await goToAnnotationJobs(page);

    await expect(page.locator('#pipelines-input')).toHaveValue('pipeline/Clinical_annotation');
    await expect(page.locator('#pipeline-editor')).toHaveClass(/loaded-editor/);
  });

  test('selected pipeline is restored on Single Annotation after round-trip navigation', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/Clinical_annotation');

    await goToAnnotationJobs(page);
    await goToSingleAnnotation(page);

    await expect(page.locator('#pipelines-input')).toHaveValue('pipeline/Clinical_annotation');
    await expect(page.locator('#pipeline-editor')).toHaveClass(/loaded-editor/);
  });

  test('temp pipeline content is restored on Annotation Jobs', async({ page }) => {
    await createTempPipeline(page);

    await goToAnnotationJobs(page);

    // No named pipeline is selected (input is empty).
    await expect(page.locator('#pipelines-input')).toBeEmpty();
    // Editor still shows the pipeline content.
    /* eslint-disable @typescript-eslint/no-unsafe-return, @typescript-eslint/no-explicit-any,
                      @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-unsafe-call */
    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const content = await page.evaluate(() =>
      (window as any).monaco.editor.getModels()[0].getValue()
    );
    /* eslint-enable */
    expect(content).toContain('allele_score');
    expect(content).toContain('hg38/scores/CADD_v1.7');
    // Status was 'loaded' on the previous page — it is preserved via the state service.
    await expect(page.locator('#pipeline-editor')).toHaveClass(/loaded-editor/);
  });

  test('temp pipeline content is restored on Single Annotation after round-trip navigation', async({ page }) => {
    await createTempPipeline(page);

    await goToAnnotationJobs(page);
    await goToSingleAnnotation(page);

    await expect(page.locator('#pipelines-input')).toBeEmpty();
    /* eslint-disable @typescript-eslint/no-unsafe-return, @typescript-eslint/no-explicit-any,
                      @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-unsafe-call */
    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const content = await page.evaluate(() =>
      (window as any).monaco.editor.getModels()[0].getValue()
    );
    /* eslint-enable */
    expect(content).toContain('allele_score');
    await expect(page.locator('#pipeline-editor')).toHaveClass(/loaded-editor/);
  });

  test('unsaved-changes indicator (*) is preserved on Annotation Jobs', async({ page }) => {
    await createAndSaveUserPipeline(page, 'My Pipeline');

    // Edit the saved pipeline to trigger the * indicator.
    /* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-member-access,
                      @typescript-eslint/no-unsafe-call, @typescript-eslint/no-unsafe-assignment */
    await page.evaluate(() => {
      const monaco = (window as any).monaco;
      const model = monaco.editor.getModels()[0];
      model.applyEdits([{ range: new monaco.Range(5, 1, 5, 1), text: '    # edited\n' }]);
    });
    /* eslint-enable */

    await expect(page.locator('#pipelines-input')).toHaveValue('My Pipeline *');

    await goToAnnotationJobs(page);

    // restoreState() recomputes isPipelineChanged() from the stored text — * should reappear.
    await expect(page.locator('#pipelines-input')).toHaveValue('My Pipeline *');
  });

  test('invalid pipeline config is preserved when navigating to Annotation Jobs', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', {
      name: 'draft New pipeline', exact: true
    }).click();
    await utils.typeInPipelineEditor(page, 'preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });
    await expect(page.getByText('Invalid configuration, reason: \'annotators\'')).toBeVisible();

    await goToAnnotationJobs(page);

    // isConfigValid signal (false) persists via the state service — button is disabled immediately.
    await expect(page.locator('#create-button')).toBeDisabled();
    // The new component instance restores the text, ngModelChange fires, re-validation runs.
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });
    await expect(page.getByText('Invalid configuration, reason: \'annotators\'')).toBeVisible();
  });

  test('editor width is preserved across navigation to Annotation Jobs and back', async({ page }) => {
    const targetWidth = `${(page.viewportSize()?.width ?? 1920) * 0.7}px`;

    await page.evaluate((width) => {
      const textarea = document.querySelector('#pipeline-editor') as HTMLTextAreaElement;
      textarea.style.width = width;
    }, targetWidth);

    await expect(page.locator('#pipeline-editor')).toHaveCSS('width', targetWidth);
    await expect(page.locator('#annotation-component')).not.toBeVisible();
    await expect(page.locator('#history-table')).not.toBeVisible();

    await goToAnnotationJobs(page);
    // ngOnDestroy saved editorWidth=70 to the state service; the new component's effect restores it.
    await expect(page.locator('#pipeline-editor')).toHaveCSS('width', targetWidth);
    await expect(page.locator('#annotation-component')).not.toBeVisible();
    await expect(page.locator('#history-table')).not.toBeVisible();

    await goToSingleAnnotation(page);
    await expect(page.locator('#pipeline-editor')).toHaveCSS('width', targetWidth);
    await expect(page.locator('#annotation-component')).not.toBeVisible();
    await expect(page.locator('#history-table')).not.toBeVisible();
  });


  test('sidebar hidden state is not preserved when navigating between pages', async({ page }) => {
    await goToAnnotationJobs(page);
    await page.locator('input[id="file-upload"]').setInputFiles('./fixtures/input-vcf-file.vcf');
    await page.locator('#create-button').click();
    await expect(page.locator('app-jobs-table')).toBeVisible();

    await goToSingleAnnotation(page);
    await page.getByPlaceholder('Type annotatable...').fill('chr1 1265232 G A');
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await expect(page.locator('app-annotatables-table')).toBeVisible();

    await page.locator('#toggle-history').click();
    await expect(page.locator('app-annotatables-table')).not.toBeVisible();

    await goToAnnotationJobs(page);
    await expect(page.locator('app-jobs-table')).toBeVisible();
  });
});

test.describe('Single annotation report state persistence across navigation', () => {
  const ANNOTATABLE = 'chr1 11796321 G A';

  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    await createTempPipeline(page);
    await page.getByPlaceholder('Type annotatable...').fill(ANNOTATABLE);
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
  });

  test('full report mode is preserved after navigating to Annotation Jobs and back', async({ page }) => {
    // Default is compact — descriptions are hidden.
    await expect(page.locator('.attribute-container .attribute-description').first()).not.toBeVisible();

    await page.locator('.switch').click();
    await expect(page.locator('.attribute-container .attribute-description').first()).toBeVisible();

    await goToAnnotationJobs(page);
    await goToSingleAnnotation(page);

    await page.getByPlaceholder('Type annotatable...').fill(ANNOTATABLE);
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    // isFullReport signal in the root state service persisted — full mode should be restored.
    await expect(page.locator('.attribute-container .attribute-description').first()).toBeVisible();
  });

  // eslint-disable-next-line max-len
  test('compact report mode is preserved after switching from full and navigating to Annotation Jobs and back', async({ page }) => {
    await page.locator('.switch').click();
    await expect(page.locator('.attribute-container .attribute-description').first()).toBeVisible();

    // Switch back to compact.
    await page.locator('.switch').click();
    await expect(page.locator('.attribute-container .attribute-description').first()).not.toBeVisible();

    await goToAnnotationJobs(page);
    await goToSingleAnnotation(page);

    await page.getByPlaceholder('Type annotatable...').fill(ANNOTATABLE);
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    // isFullReport=false was preserved in the root state service — compact mode should be restored.
    await expect(page.locator('.attribute-container .attribute-description').first()).not.toBeVisible();
  });
});

test.describe('Single annotation report state reset on authentication change', () => {
  // A variant that produces a report with the default public pipeline (Autism_annotation).
  const ANNOTATABLE = 'chr1 1265232 G A';

  async function switchToFullMode(page: Page): Promise<void> {
    await page.locator('.switch').click();
    await expect(page.locator('.attribute-container .attribute-description').first()).toBeVisible();
  }

  test('report state resets to compact after logout and re-login', async({ page }) => {
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill(ANNOTATABLE);
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await switchToFullMode(page);

    // Logout triggers window.location.reload(), which re-initialises all Angular services.
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'load' }),
      page.locator('#logout-button').click(),
    ]);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill(ANNOTATABLE);
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    // isFullReport is reset to false on page reload — compact mode should be the default.
    await expect(page.locator('.attribute-container .attribute-description').first()).not.toBeVisible();
  });

  test('report state resets to compact when anonymous user with full mode logs in', async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill(ANNOTATABLE);
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });
    await switchToFullMode(page);

    // Registering navigates to /register then to the confirmation link — full page reloads
    // that re-initialise Angular and reset isFullReport to false.
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.getByPlaceholder('Type annotatable...').fill(ANNOTATABLE);
    await page.getByRole('button', { name: 'Go', exact: true }).click();
    await page.waitForSelector('#report', { timeout: 120000 });

    // isFullReport is reset to false on page reload — compact mode should be the default.
    await expect(page.locator('.attribute-container .attribute-description').first()).not.toBeVisible();
  });
});

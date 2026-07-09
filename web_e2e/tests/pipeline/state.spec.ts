import { test, expect, Page } from '@playwright/test';
import * as utils from '../../utils';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import { SingleAnnotation } from '../../pages/single-annotation.page';
import { AnnotationJobs } from '../../pages/annotation-jobs.page';

const VALID_PIPELINE =
  'preamble:\n' +
  '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
  'annotators:\n' +
  '- allele_score:\n' +
  '    resource_id: hg38/scores/CADD_v1.7\n';

async function readEditorContent(page: Page): Promise<string> {
  // window.monaco is only assigned once the editor has loaded, which can lag
  // behind the .loaded-editor class right after login and after a reload.
  /* eslint-disable @typescript-eslint/no-explicit-any, @typescript-eslint/no-unsafe-member-access,
                    @typescript-eslint/no-unsafe-call, @typescript-eslint/no-unsafe-return */
  await page.waitForFunction(
    () => Boolean((window as any).monaco?.editor?.getModels?.()?.[0]?.getValue?.().length),
    undefined,
    { timeout: 120000 },
  );
  // eslint-disable-next-line @typescript-eslint/no-unsafe-return
  return page.evaluate(() => (window as any).monaco.editor.getModels()[0].getValue());
  /* eslint-enable */
}

async function goToAnnotationJobs(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'Annotation Jobs' }).click();
  await page.waitForSelector('app-annotation-jobs-wrapper', { timeout: 30000 });
}

async function goToSingleAnnotation(page: Page): Promise<void> {
  await page.getByRole('link', { name: 'Single Annotation' }).click();
  await page.waitForSelector('app-single-annotation-wrapper', { timeout: 30000 });
}

async function createTempPipeline(page: Page): Promise<void> {
  const editor = new PipelineEditor(page);
  await editor.newPipeline();

  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), { timeout: 30000 }
  );
  await utils.typeInPipelineEditor(page, VALID_PIPELINE);
  await saveResponse;
  await PipelineEditor.waitForLoaded(page);
}

async function createAndSaveUserPipeline(page: Page, name: string): Promise<void> {
  const editor = new PipelineEditor(page);
  await createTempPipeline(page);

  await editor.saveAs();
  await expect(editor.nameModal).toBeVisible();
  await editor.nameInput.fill(name);

  // Click and wait for the saveAs's POST specifically (not /pipelines/load).
  // /pipelines/load is fired by an effect on selectedPipelineId change AND
  // also by the temp pipeline's autoSave inside createTempPipeline. The
  // temp-pipeline load can still be in flight when this Promise.all arms
  // its waitForResponse, so a `/load` match could catch the stale response
  // and let the test continue before selectPipelineAfterSave runs — leaving
  // selectedPipeline=null and currentPipelineText pre-edit at the moment a
  // subsequent Monaco edit fires (no `*` is added because
  // displayUnsavedPipelineIndication early-returns on null selectedPipeline).
  await Promise.all([
    editor.saveNameButton.click(),
    page.waitForResponse(
      resp => resp.url().includes('api/pipelines/user') && resp.request().method() === 'POST'
    ),
  ]);

  // Wait for the post-save selectPipelineAfterSave to finish: dropdown
  // carries the saved pipeline name, and the editor is in 'loaded'.
  await expect(editor.pipelineInput).toHaveValue(name, { timeout: 30000 });
  await PipelineEditor.waitForLoaded(page);
}

test.describe('Annotation pipeline state persistence across navigation', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
  });

  test('selected pipeline is restored on Annotation Jobs after navigating from Single Annotation', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.selectPipeline('pipeline/hg38_clinical_annotation');
    await expect(editor.pipelineInput).toHaveValue('pipeline/hg38_clinical_annotation');

    await goToAnnotationJobs(page);

    await expect(editor.pipelineInput).toHaveValue('pipeline/hg38_clinical_annotation');
    await expect(page.locator('#pipeline-editor')).toHaveClass(/loaded-editor/);
  });

  test('selected pipeline is restored on Single Annotation after round-trip navigation', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.selectPipeline('pipeline/hg38_clinical_annotation');

    await goToAnnotationJobs(page);
    await goToSingleAnnotation(page);

    await expect(editor.pipelineInput).toHaveValue('pipeline/hg38_clinical_annotation');
    await expect(page.locator('#pipeline-editor')).toHaveClass(/loaded-editor/);
  });

  test('temp pipeline content is restored on Annotation Jobs', async({ page }) => {
    const editor = new PipelineEditor(page);
    await createTempPipeline(page);

    await goToAnnotationJobs(page);

    // No named pipeline is selected (input is empty).
    await expect(editor.pipelineInput).toBeEmpty();
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
    const editor = new PipelineEditor(page);
    await createTempPipeline(page);

    await goToAnnotationJobs(page);
    await goToSingleAnnotation(page);

    await expect(editor.pipelineInput).toBeEmpty();
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
    const editor = new PipelineEditor(page);
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

    await expect(editor.pipelineInput).toHaveValue('My Pipeline *');

    await goToAnnotationJobs(page);

    // restoreState() recomputes isPipelineChanged() from the stored text — * should reappear.
    await expect(editor.pipelineInput).toHaveValue('My Pipeline *');
  });

  test('invalid pipeline config is preserved when navigating to Annotation Jobs', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();
    await utils.typeInPipelineEditor(page, 'preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });
    await expect(page.getByText('Invalid configuration, reason: \'annotators\'')).toBeVisible();

    await goToAnnotationJobs(page);

    // isConfigValid signal (false) persists via the state service — button is disabled immediately.
    await expect(new AnnotationJobs(page).createButton).toBeDisabled();
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
    const jobs = new AnnotationJobs(page);
    const singleAnnotation = new SingleAnnotation(page);
    await goToAnnotationJobs(page);
    await jobs.uploadFile('./fixtures/input-vcf-file.vcf');
    await jobs.create();
    await expect(page.locator('app-jobs-table')).toBeVisible();

    await goToSingleAnnotation(page);
    await singleAnnotation.annotate('chr1 1265232 G A');
    await expect(page.locator('app-annotatables-table')).toBeVisible();

    await page.locator('#toggle-history').click();
    await expect(page.locator('app-annotatables-table')).not.toBeVisible();

    await goToAnnotationJobs(page);
    await expect(page.locator('app-jobs-table')).toBeVisible();
  });
});

test.describe('Annotation pipeline state on browser refresh', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
  });

  test('reloads the default pipeline name and content, not the unsaved temporary one', async({ page }) => {
    const editor = new PipelineEditor(page);
    // Capture the default pipeline the app loads on start.
    const defaultName = await editor.pipelineInput.inputValue();
    expect(defaultName).not.toBe('');
    const defaultContent = await readEditorContent(page);

    // Create an unsaved temporary pipeline (dropdown empties; editor shows the temp).
    await createTempPipeline(page);
    await expect(editor.pipelineInput).toBeEmpty();
    expect(await readEditorContent(page)).toContain('hg38/scores/CADD_v1.7');

    // Refresh the browser. The state signals are wiped; the backend still holds
    // the session temp pipeline and resyncs its status over the socket on
    // reconnect, but the editor must reselect and load the default pipeline.
    await page.reload({ waitUntil: 'load' });
    await PipelineEditor.waitForLoaded(page);

    await expect(editor.pipelineInput).toHaveValue(defaultName);
    // Editor shows the default content again, not the temporary pipeline's.
    expect(await readEditorContent(page)).toBe(defaultContent);
  });

  test('drives the status bar from the default pipeline, not the temporary one', async({ page }) => {
    const editor = new PipelineEditor(page);
    const defaultName = await editor.pipelineInput.inputValue();
    // Wait for the default pipeline info to populate the status bar, then capture it.
    await expect(editor.statusItem(0)).not.toHaveText('menu0 annotators');
    const defaultAnnotators = await editor.statusItem(0).textContent() ?? '';
    const defaultAttributes = await editor.statusItem(1).textContent() ?? '';

    // Create an unsaved temporary pipeline whose status bar shows a single annotator.
    await createTempPipeline(page);
    await expect(editor.statusItem(0)).toHaveText('menu1 annotators');

    // Refresh: the status bar must reflect the reloaded default pipeline, not the
    // stale temp resynced over the socket. That resync previously fired a second
    // pipeline_status request for the temp that overrode the default's counts.
    await page.reload({ waitUntil: 'load' });
    await PipelineEditor.waitForLoaded(page);

    await expect(editor.pipelineInput).toHaveValue(defaultName);
    await expect(editor.statusItem(0)).toHaveText(defaultAnnotators);
    await expect(editor.statusItem(1)).toHaveText(defaultAttributes);
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
    await PipelineEditor.waitForLoaded(page);
    await createTempPipeline(page);
    await new SingleAnnotation(page).annotate(ANNOTATABLE);
  });

  test('full report mode is preserved after navigating to Annotation Jobs and back', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    // Default is compact — descriptions are hidden.
    await expect(singleAnnotation.attributeDescriptions.first()).not.toBeVisible();

    await singleAnnotation.toggleFullReport();
    await expect(singleAnnotation.attributeDescriptions.first()).toBeVisible();

    await goToAnnotationJobs(page);
    await goToSingleAnnotation(page);

    await singleAnnotation.annotate(ANNOTATABLE);

    // isFullReport signal in the root state service persisted — full mode should be restored.
    await expect(singleAnnotation.attributeDescriptions.first()).toBeVisible();
  });

  // eslint-disable-next-line max-len
  test('compact report mode is preserved after switching from full and navigating to Annotation Jobs and back', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.toggleFullReport();
    await expect(singleAnnotation.attributeDescriptions.first()).toBeVisible();

    // Switch back to compact.
    await singleAnnotation.toggleFullReport();
    await expect(singleAnnotation.attributeDescriptions.first()).not.toBeVisible();

    await goToAnnotationJobs(page);
    await goToSingleAnnotation(page);

    await singleAnnotation.annotate(ANNOTATABLE);

    // isFullReport=false was preserved in the root state service — compact mode should be restored.
    await expect(singleAnnotation.attributeDescriptions.first()).not.toBeVisible();
  });
});

test.describe('Single annotation report state reset on authentication change', () => {
  // A variant that produces a report with the default public pipeline (Autism_annotation).
  const ANNOTATABLE = 'chr1 1265232 G A';

  async function switchToFullMode(page: Page): Promise<void> {
    const singleAnnotation = new SingleAnnotation(page);
    await singleAnnotation.toggleFullReport();
    await expect(singleAnnotation.attributeDescriptions.first()).toBeVisible();
  }

  test('report state resets to compact after logout and re-login', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);

    await singleAnnotation.annotate(ANNOTATABLE);
    await switchToFullMode(page);

    // Logout triggers window.location.reload(), which re-initialises all Angular services.
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'load' }),
      page.locator('#logout-button').click(),
    ]);
    await PipelineEditor.waitForLoaded(page);

    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);

    await singleAnnotation.annotate(ANNOTATABLE);

    // isFullReport is reset to false on page reload — compact mode should be the default.
    await expect(singleAnnotation.attributeDescriptions.first()).not.toBeVisible();
  });

  test('report state resets to compact when anonymous user with full mode logs in', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await page.goto('/', { waitUntil: 'load' });
    await PipelineEditor.waitForLoaded(page);

    await singleAnnotation.annotate(ANNOTATABLE);
    await switchToFullMode(page);

    // Registering navigates to /register then to the confirmation link — full page reloads
    // that re-initialise Angular and reset isFullReport to false.
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);

    await singleAnnotation.annotate(ANNOTATABLE);

    // isFullReport is reset to false on page reload — compact mode should be the default.
    await expect(singleAnnotation.attributeDescriptions.first()).not.toBeVisible();
  });
});

test.describe('Pipeline list updates on authentication change', () => {
  const USER_PIPELINE_NAME = 'auth-change-test-pipeline';

  async function getPipelineOptions(page: Page): Promise<string[]> {
    const editor = new PipelineEditor(page);
    await editor.dropdownIcon.click();
    await page.waitForSelector('mat-option', { state: 'visible', timeout: 10000 });
    const options = await page.getByRole('option').allTextContents();
    await page.keyboard.press('Escape');
    return options;
  }

  test('only default pipelines are visible after logout', async({ page }) => {
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
    await createAndSaveUserPipeline(page, USER_PIPELINE_NAME);

    // User pipeline is present before logout.
    const optionsBefore = await getPipelineOptions(page);
    expect(optionsBefore.some(o => o.includes(USER_PIPELINE_NAME))).toBe(true);

    // Logout triggers window.location.reload().
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'load' }),
      page.locator('#logout-button').click(),
    ]);
    await PipelineEditor.waitForLoaded(page);

    const optionsAfter = await getPipelineOptions(page);
    expect(optionsAfter.some(o => o.includes(USER_PIPELINE_NAME))).toBe(false);
    expect(optionsAfter.some(o => o.includes('pipeline/'))).toBe(true);
  });

  test('user pipelines appear after in-app login when anonymous pipelines were cached', async({ page }) => {
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';

    // Create the user pipeline while logged in via full-page login.
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
    await createAndSaveUserPipeline(page, USER_PIPELINE_NAME);

    // Logout (full page reload) — now anonymous.
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'load' }),
      page.locator('#logout-button').click(),
    ]);
    await PipelineEditor.waitForLoaded(page);

    // Populate the state with anonymous (default-only) pipelines by navigating
    // between tabs. Both directions load getPipelines() and cache the result.
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
    await page.waitForSelector('app-annotation-jobs-wrapper', { timeout: 30000 });
    await PipelineEditor.waitForLoaded(page);
    await page.getByRole('link', { name: 'Single Annotation' }).click();
    await page.waitForSelector('app-single-annotation-wrapper', { timeout: 30000 });
    await PipelineEditor.waitForLoaded(page);

    // Login via the in-app button — this is a client-side router.navigate(['/login']),
    // so the AnnotationPipelineStateService singleton is preserved with the stale
    // anonymous pipeline cache. Without the fix this would cause getPipelines() to
    // skip the fetch and show only the cached default pipelines after login.
    await page.locator('#login-button').click();
    await page.locator('#email').pressSequentially(email);
    await page.locator('#password').pressSequentially(password);
    await page.locator('#login-container').getByRole('button', { name: 'Login' }).click();
    await page.waitForSelector('app-single-annotation-wrapper', { timeout: 120000 });
    await PipelineEditor.waitForLoaded(page);

    // Navigate to Annotation Jobs — triggers getPipelines() with isUserLoggedIn=true
    // against a state where loadedWhileLoggedIn=false, so a fresh fetch must fire.
    await page.getByRole('link', { name: 'Annotation Jobs' }).click();
    await page.waitForSelector('app-annotation-jobs-wrapper', { timeout: 30000 });
    await PipelineEditor.waitForLoaded(page);

    const options = await getPipelineOptions(page);
    expect(options.some(o => o.includes(USER_PIPELINE_NAME))).toBe(true);
    expect(options.some(o => o.includes('pipeline/'))).toBe(true);
  });
});

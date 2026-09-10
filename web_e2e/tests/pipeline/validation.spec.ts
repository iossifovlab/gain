import { test, expect } from '@playwright/test';
import * as utils from '../../utils';
import { PipelineEditor } from '../../pages/pipeline-editor.page';

test.describe('Pipeline validation tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await PipelineEditor.waitForLoaded(page);
  });

  test('should type config without annotators and show error message', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();
    await utils.typeInPipelineEditor(page, 'preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });
    await expect(page.getByText('Invalid configuration, reason: \'annotators\'')).toBeVisible();
  });

  test('should type config without peamble and show error message', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();
    await utils.typeInPipelineEditor(page, 'annotators:\n - allele_score: hg38/scores/CADD_v1.7');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });
    await expect(page.getByText('Invalid configuration, reason: \'preamble\'')).toBeVisible();
  });

  test('should type semantically invalid config and display error', async({ page }) => {
    const editor = new PipelineEditor(page);
    await editor.newPipeline();
    await utils.typeInPipelineEditor(page, '- allele_score');

    // Two assertions, and deliberately neither of them spans the middle of
    // the message: everything between them is the AnnotatorInfo repr, which
    // is this test's own input echoed back (annotator id, type, attributes,
    // and a work_dir minted lazily by tempfile.mkdtemp, see gain#331, whose
    // random suffix changes every run). Pinning it asserts what we typed
    // rather than what the server refused, and it rotted once already when
    // the refusal was reworded (gain#1353).
    await expect(editor.errorMessage).toContainText(
      'Invalid configuration, reason: ' +
      'The A0 annotator configuration is incorrect:');
    // The refusal clause, whole and contiguous: it sits after the repr, so
    // it can be asserted in one piece, and the repr cannot satisfy it.
    await expect(editor.errorMessage).toContainText(
      'needs a \'resource_id\' parameter naming the resource ' +
      'the annotator reads.');
  });

  test('should show a resource-not-found error for a config referencing a missing resource', async({ page }) => {
    const editor = new PipelineEditor(page);
    // Resource resolution happens during validation, so a config that is
    // otherwise valid but points at a non-existent resource is surfaced as an
    // invalid config (not the async 'failed' build state).
    await editor.newPipeline();
    await utils.typeInPipelineEditor(
      page,
      'preamble:\n' +
      '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
      'annotators:\n' +
      '- allele_score:\n' +
      '    resource_id: hg38/scores/THIS_RESOURCE_DOES_NOT_EXIST\n' +
      '    input_annotatable: normalized_allele\n'
    );
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });

    await expect(editor.errorMessage).toContainText('Invalid configuration');
    await expect(editor.errorMessage).toContainText('hg38/scores/THIS_RESOURCE_DOES_NOT_EXIST');
    await expect(editor.errorMessage).toContainText('not found');
  });
});

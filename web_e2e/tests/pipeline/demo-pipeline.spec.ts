import { test, expect } from '@playwright/test';
import * as utils from '../../utils';
import { PipelineEditor } from '../../pages/pipeline-editor.page';

// `pipeline/hg38_demo_pipeline` selects ENCODE resources with the wildcard
// `TF*[target="CREB*"]` (iossifovlab/grr@7038fcc43). Those resources live
// only in `grr_encode`, so the pipeline loads only when the stack reads the
// group of `grr` and `grr_encode`, as production does (gain#2415). With the
// main GRR alone it loads as failed: "Invalid configuration, reason: No
// resources match the wildcard 'TF*'".
test.describe('Demo pipeline', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});
    // wait for default pipeline to load
    await PipelineEditor.waitForLoaded(page);
  });

  test('should load the public demo pipeline without a config error', async({ page }) => {
    const editor = new PipelineEditor(page);

    await utils.selectPipeline(page, 'pipeline/hg38_demo_pipeline');

    await expect(editor.pipelineInput).toHaveValue('pipeline/hg38_demo_pipeline');
    await expect(page.locator('.loaded-editor')).toBeVisible();
    await expect(page.locator('.invalid-config')).toHaveCount(0);
    // A loaded pipeline renders no error element at all, so count the
    // matches instead of reading the text of an element that is absent.
    await expect(editor.errorMessage.filter({ hasText: 'Invalid configuration' })).toHaveCount(0);
  });
});

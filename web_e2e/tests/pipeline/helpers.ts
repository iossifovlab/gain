import { expect, Page } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import * as utils from '../../utils';

export async function customDefaultPipeline(page: Page): Promise<void> {
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
}

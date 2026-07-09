import { expect, Page } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import * as utils from '../../utils';

export async function effectAnnotatorPipeline(page: Page): Promise<void> {
  const editor = new PipelineEditor(page);
  await editor.newPipeline();
  await expect(editor.pipelineInput).toBeEmpty();
  await expect(editor.monacoEditor.nth(0)).toBeEmpty();

  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), { timeout: 30000 }
  );

  await utils.typeInPipelineEditor(
    page,
    '- effect_annotator:\n' +
    '    gene_models: hg38/gene_models/MANE/1.4\n' +
    '    genome: hg38/genomes/GRCh38.p14\n' +
    '    attributes:\n' +
    '    - name: gene_effects\n' +
    '      source: gene_effects\n' +
    '      internal: false\n' +
    '    - name: effect_details\n' +
    '      source: effect_details\n' +
    '      internal: false\n' +
    '    - name: gene_list\n' +
    '      source: gene_list\n' +
    '      internal: false\n'
  );

  await saveResponse;

  await PipelineEditor.waitForLoaded(page);
}

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
    '- normalize_allele_annotator:\n' +
    '    genome: hg38/genomes/GRCh38-hg38\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/scores/dbSNP\n' +
    '    input_annotatable: normalized_allele\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL\n' +
    '    input_annotatable: normalized_allele\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/variant_frequencies/gnomAD_4.1.0/genomes/ALL\n' +
    '    input_annotatable: normalized_allele\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/scores/ClinVar_20240730\n' +
    '    input_annotatable: normalized_allele\n'
  );

  await saveResponse;

  await PipelineEditor.waitForLoaded(page);
}

export async function strTypePipeline(page: Page): Promise<void> {
  const editor = new PipelineEditor(page);
  await editor.newPipeline();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );
  await utils.typeInPipelineEditor(
    page,
    '- effect_annotator:\n' +
    '    gene_models: hg38/gene_models/GENCODE/48/basic/ALL\n' +
    '    genome: hg38/genomes/GRCh38.p13\n' +
    '    attributes:\n' +
    '    - name: worst_effect\n' +
    '      source: worst_effect\n' +
    '      internal: false\n'
  );
  await saveResponse;
  await PipelineEditor.waitForLoaded(page);
}

export async function floatTypePipeline(page: Page): Promise<void> {
  const editor = new PipelineEditor(page);
  await editor.newPipeline();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );
  await utils.typeInPipelineEditor(
    page,
    '- normalize_allele_annotator:\n' +
    '    genome: hg38/genomes/GRCh38-hg38\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/variant_frequencies/gnomAD_4.1.0/exomes/ALL\n' +
    '    input_annotatable: normalized_allele\n'
  );
  await saveResponse;
  await PipelineEditor.waitForLoaded(page);
}

export async function annotatableTypePipeline(page: Page): Promise<void> {
  const editor = new PipelineEditor(page);
  await editor.newPipeline();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );
  await utils.typeInPipelineEditor(
    page,
    '- normalize_allele_annotator:\n' +
    '    genome: hg38/genomes/GRCh38-hg38\n' +
    '    attributes:\n' +
    '    - name: normalized_allele\n' +
    '      source: normalized_allele\n' +
    '      internal: false\n'
  );
  await saveResponse;
  await PipelineEditor.waitForLoaded(page);
}


export async function objectTypePipeline(page: Page): Promise<void> {
  const editor = new PipelineEditor(page);
  await editor.newPipeline();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), {timeout: 30000}
  );
  await utils.typeInPipelineEditor(
    page,
    '- effect_annotator:\n' +
    '   gene_models: hg38/gene_models/GENCODE/48/basic/PRI\n' +
    '   genome: hg38/genomes/GRCh38.p14\n' +
    '   attributes:\n' +
    '   - worst_effect\n' +
    '   - name: gene_list \n' +
    '     internal: true\n' +
    '- gene_score_annotator:\n' +
    '   resource_id: gene_properties/gene_scores/SFARI_gene_score_2024_Q1\n' +
    '   input_gene_list: gene_list\n' +
    '   attributes:\n' +
    '   - name: SFARI_gene_score\n' +
    '     source: SFARI Gene Score\n'
  );
  await saveResponse;
  await PipelineEditor.waitForLoaded(page);
}


export async function clinvarListPipeline(page: Page): Promise<void> {
  const editor = new PipelineEditor(page);
  await editor.newPipeline();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), { timeout: 30000 }
  );
  await utils.typeInPipelineEditor(
    page,
    '- normalize_allele_annotator:\n' +
    '    genome: hg38/genomes/GRCh38-hg38\n' +
    '\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/scores/ClinVar_20240730\n' +
    '    input_annotatable: normalized_allele\n' +
    '    mode: region\n' +
    '    attributes:\n' +
    '    - name: clnsig_list\n' +
    '      source: CLNSIG\n' +
    '      aggregator: list\n'
  );
  await saveResponse;
  await PipelineEditor.waitForLoaded(page);
}

export async function caddListPipeline(page: Page): Promise<void> {
  const editor = new PipelineEditor(page);
  await editor.newPipeline();
  const saveResponse = page.waitForResponse(
    resp => resp.url().includes('api/pipelines/user'), { timeout: 30000 }
  );
  await utils.typeInPipelineEditor(
    page,
    'preamble:\n' +
    '   input_reference_genome: hg38/genomes/GRCh38-hg38\n' +
    'annotators:\n' +
    '- allele_score:\n' +
    '    resource_id: hg38/scores/CADD_v1.7\n' +
    '    mode: region\n' +
    '    attributes:\n' +
    '    - name: cadd_raw_list\n' +
    '      source: cadd_raw\n' +
    '      aggregator: list\n'
  );
  await saveResponse;
  await PipelineEditor.waitForLoaded(page);
}

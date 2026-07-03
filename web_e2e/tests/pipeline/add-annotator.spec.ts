import { test, expect } from '@playwright/test';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Add annotator to pipeline tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000});
  });

  test('should open new annotator dialog with correct header and first step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await expect(page.locator('mat-dialog-container')).toBeVisible();
    await expect(page.locator('#modal-header')).toHaveText('New annotator');
    await expect(page.getByRole('combobox', { name: 'Select annotator' })).toBeVisible();
  });

  test('should append gene set annotator', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('gene_set_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="resource_id-dropdown"]').click();
    await page.locator('mat-option').getByText('gene_properties/gene_sets/autism').click();
    await page.locator('[id="input_gene_list-dropdown"]').click();
    await page.locator('mat-option').getByText('gene_list').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('.attribute-source')).toHaveCount(1);

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/validate')
      ),
    ]);

    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const value = await page.evaluate(() => {
      // eslint-disable-next-line max-len
      // eslint-disable-next-line @typescript-eslint/no-unsafe-return, @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-unsafe-call, @typescript-eslint/no-explicit-any
      return (window as any).monaco.editor.getModels()[0].getValue();
    });

    expect(value).toContain(
      '- gene_set_annotator:\n'+
      '    resource_id: gene_properties/gene_sets/autism\n' +
      '    input_gene_list: gene_list\n'+
      '    attributes:\n'+
      '    - name: in_sets\n'+
      '      source: in_sets\n' +
      '      internal: false\n'
    );
  });

  test('should append two annotators', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('liftover_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="chain-dropdown"]').click();
    await page.locator('mat-option').getByText('liftover/hg19_to_T2T').click();

    await page.locator('[id="source_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('t2t/genomes/t2t-chm13v2.0').click();

    await page.locator('[id="target_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/genomes/GRCh38.p14').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/validate')
      ),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/user'), // wait for pipeline to be saved
        {timeout: 20000}, // hg38_to_t2t chain loading can take a while, increase timeout for this test
      ),
    ]);

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });

    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('position_score_annotator').click();
    await page.waitForTimeout(3000);
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="resource_id-dropdown"]').click();
    await page.locator('mat-option').getByText('hg19/scores/FitCons2/E050').click();

    await page.getByRole('button', { name: 'Next' }).click();

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/validate')
      ),
    ]);

    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const value = await page.evaluate(() => {
      // eslint-disable-next-line max-len
      // eslint-disable-next-line @typescript-eslint/no-unsafe-return, @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-unsafe-call, @typescript-eslint/no-explicit-any
      return (window as any).monaco.editor.getModels()[0].getValue();
    });

    expect(value).toContain(
      '- liftover_annotator:\n' +
      '    chain: liftover/hg19_to_T2T\n' +
      '    source_genome: t2t/genomes/t2t-chm13v2.0\n' +
      '    target_genome: hg38/genomes/GRCh38.p14\n' +
      '    attributes:\n' +
      '    - name: liftover_annotatable\n' +
      '      source: liftover_annotatable\n' +
      '      internal: true\n' +
      '\n' +
      '- position_score_annotator:\n' +
      '    resource_id: hg19/scores/FitCons2/E050\n' +
      '    attributes:\n' +
      '    - name: FitCons2_E050\n' +
      '      source: FitCons2_E050\n' +
      '      internal: false'
    );
  });

  test('should append annotator to user pipeline', async({ page }) => {
    await customDefaultPipeline(page);

    await page.getByRole('button', { name: 'Save as' }).click();

    await expect(page.locator('#name-modal')).toBeVisible();
    await page.locator('#name-modal input').fill('My Pipeline');

    await Promise.all([
      page.locator('#name-modal').getByRole('button', { name: 'Save' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/load') // wait for pipeline to be saved and loaded
      ),
      page.waitForResponse(
        resp => resp.url().includes('api/editor/pipeline_status?pipeline_id'),
        {timeout: 300000}
      )
    ]);

    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
    // append new annotator
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();

    await page.getByRole('button', { name: 'Next' }).click();

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/validate')
      ),
    ]);

    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const value = await page.evaluate(() => {
      // eslint-disable-next-line max-len
      // eslint-disable-next-line @typescript-eslint/no-unsafe-return, @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-unsafe-call, @typescript-eslint/no-explicit-any
      return (window as any).monaco.editor.getModels()[0].getValue();
    });

    expect(value).toContain(
      'preamble:\n' +
      '   input_reference_genome: hg38/genomes/GRCh38-hg38' +
      '\n' +
      'annotators:\n' +
      '- allele_score:\n' +
      '    resource_id: hg38/scores/CADD_v1.7\n' +
      '\n' +
      '- simple_effect_annotator:\n' +
      '    gene_models: hg38/gene_models/GENCODE/46/basic/PRI\n' +
      '    attributes:\n' +
      '    - name: worst_effect\n' +
      '      source: worst_effect\n' +
      '      internal: false\n' +
      '    - name: worst_effect_genes\n' +
      '      source: worst_effect_genes\n' +
      '      internal: false\n' +
      '    - name: gene_list\n' +
      '      source: gene_list\n' +
      '      internal: true\n'
    );
  });

  test('should disable Next button when no annotator is selected and enable it after selection', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('allele_score').click();

    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  test('should filter annotators in dropdown by search text', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).fill('allele');
    await page.getByRole('combobox', { name: 'allele' }).dispatchEvent('input');

    await expect(page.locator('.annotator-option')).toHaveCount(2);
    await expect(page.locator('.annotator-option').filter({ hasText: 'allele_score_annotator' })).toBeVisible();
    await expect(page.locator('.annotator-option').filter({ hasText: 'normalize_allele_annotator' })).toBeVisible();
  });

  test('should navigate back from configure step to annotator selection step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('allele_score').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('[id="resource_id-dropdown"]')).toBeVisible();

    await page.getByRole('button', { name: 'Back' }).click();

    await expect(page.getByRole('combobox', { name: 'allele_score_annotator' })).toBeVisible();
  });

  test('should check selected data in summary panel', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('allele_score').click();
    await page.getByRole('button', { name: 'Next' }).click();

    // configure step: summary shows the selected annotator
    const configureSummary = page.locator('.mat-horizontal-stepper-content-current');
    await expect(configureSummary.locator('.annotator-display-text')).toContainText('annotator');
    await expect(configureSummary.locator('.annotator-display-text')).toContainText('allele_score_annotator');

    await page.locator('[id="resource_id-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/scores/CADD_v1.7').click();
    await page.getByRole('button', { name: 'Next' }).click();

    // attribute step: summary adds the configured resource_id beneath the annotator
    const attributeSummary = page.locator('.mat-horizontal-stepper-content-current');
    await expect(attributeSummary.locator('.annotator-display-text')).toContainText('allele_score_annotator');
    const resourceIdDisplay = attributeSummary.locator('.resources-display-text').filter({ hasText: 'resource_id' });
    await expect(resourceIdDisplay).toContainText('hg38/scores/CADD_v1.7');
  });

  test('should remove a default attribute in the attribute step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('.attribute-source')).toHaveCount(3);

    await page.locator('#gene_list-remove-button').click();

    await expect(page.locator('.attribute-source')).toHaveCount(2);
  });

  test('should rename attribute and reflect new name in finished YAML', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('.editable-name').first().fill('my_worst_effect');
    await page.locator('.editable-name').first().blur();

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/validate')),
    ]);

    // eslint-disable-next-line @typescript-eslint/no-unsafe-assignment
    const value = await page.evaluate(() => {
      // eslint-disable-next-line max-len
      // eslint-disable-next-line @typescript-eslint/no-unsafe-return, @typescript-eslint/no-unsafe-member-access, @typescript-eslint/no-unsafe-call, @typescript-eslint/no-explicit-any
      return (window as any).monaco.editor.getModels()[0].getValue();
    });

    expect(value).toContain('name: my_worst_effect');
    expect(value).not.toContain('name: worst_effect\n');
  });

  test('should show duplicate attribute name error and disable Finish button', async({ page }) => {
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('.attribute-source')).toHaveCount(3);

    await page.locator('.editable-name').first().fill('worst_effect_genes');
    await page.locator('.editable-name').first().blur();

    await expect(page.locator('.error-message')).toContainText('Attribute with this name already exists');
    await expect(page.getByRole('button', { name: 'Finish' })).toBeDisabled();
  });

  test('should disable New annotator button when pipeline config is invalid', async({ page }) => {
    await page.locator('#pipeline-actions').getByRole('button', { name: 'draft New pipeline', exact: true }).click();
    await utils.typeInPipelineEditor(page, 'preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });

    await expect(page.locator('#pipeline-actions').locator('#add-annotator-button')).toBeDisabled();
  });

  test('should enable Next button on configure step only after all required fields are filled', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('liftover_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="chain-dropdown"]').click();
    await page.locator('mat-option').getByText('liftover/hg19_to_T2T').click();
    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();

    await page.locator('[id="source_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('t2t/genomes/t2t-chm13v2.0').click();
    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();

    await page.locator('[id="target_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/genomes/GRCh38.p14').click();
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  test('should enable Next when only required field is filled', async({ page }) => {
    // effect_annotator has gene_models (required) and genome (optional resource)
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('effect_annotator', { exact: true }).click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('[id="gene_models-dropdown"]')).toBeVisible();
    await expect(page.locator('[id="genome-dropdown"]')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();

    // Next is enabled without filling optional genome field
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  test('should keep Next enabled after filling an optional resource field', async({ page }) => {
    // effect_annotator: fill required gene_models then also fill optional genome
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('effect_annotator', { exact: true }).click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();

    await page.locator('[id="genome-dropdown"]').click();
    await page.locator('mat-option.resource-option').first().click();
    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();
  });

  test('should filter chain options by search text in configure step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('liftover_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="chain-dropdown"]').locator('.dropdown-icon').click();

    await page.locator('[id="chain-dropdown"] input').pressSequentially('hg19');
    const filteredOptions = page.locator('mat-option.resource-option');

    expect(await filteredOptions.count()).toBe(4);
  });

  test('should filter source_genome options by search text in configure step', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('liftover_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="source_genome-dropdown"]').locator('.dropdown-icon').click();

    await page.locator('[id="source_genome-dropdown"] input').pressSequentially('t2t');
    const filteredOptions = page.locator('mat-option.resource-option');

    expect(await filteredOptions.count()).toBe(1);
  });

  test('should disable Next button in configure step when a filled required field is cleared', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('liftover_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="chain-dropdown"]').click();
    await page.locator('mat-option').getByText('liftover/hg19_to_T2T').click();
    await page.locator('[id="source_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('t2t/genomes/t2t-chm13v2.0').click();
    await page.locator('[id="target_genome-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/genomes/GRCh38.p14').click();

    await expect(page.getByRole('button', { name: 'Next' })).toBeEnabled();

    // clicking the dropdown icon clears the field and opens the panel
    await page.locator('[id="chain-dropdown"]').locator('.dropdown-icon').click();
    await page.keyboard.press('Escape');

    await expect(page.getByRole('button', { name: 'Next' })).toBeDisabled();
  });

  test('should toggle attribute internal flag and reflect it in finished YAML', async({ page }) => {
    await customDefaultPipeline(page);
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    // gene_list is the 3rd attribute (index 2) and internal: true by default
    const geneListCheckbox = page.locator('.attribute-internal input[type="checkbox"]').nth(2);
    await expect(geneListCheckbox).toBeChecked();
    await geneListCheckbox.click();
    await expect(geneListCheckbox).not.toBeChecked();

    await Promise.all([
      page.getByRole('button', { name: 'Finish' }).click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/validate')),
    ]);

    /* eslint-disable */
    const value = await page.evaluate(() => {
      return (window as any).monaco.editor.getModels()[0].getValue();
    });
    /* eslint-enable */

    expect(value).toContain('name: gene_list\n      source: gene_list\n      internal: false');
  });

  // eslint-disable-next-line max-len
  test('should select all attributes, remove all, then select one from dropdown as the only selected', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('.attribute-source')).toHaveCount(3);

    await page.getByRole('button', { name: 'Select all' }).click();
    await expect(page.locator('.attributes-section-label')).toContainText('(15)');

    await page.getByRole('button', { name: 'Remove all' }).click();
    await expect(page.locator('.attributes-section-label')).not.toBeVisible();

    await page.locator('#attributes-dropdown .dropdown-icon').click();
    await page.locator('mat-option.attribute-option').first().click();

    await expect(page.locator('.attribute-source')).toHaveCount(1);
    await expect(page.locator('.attribute-source').first()).toHaveText('worst_effect');
  });

  test('should filter attributes in the dropdown by search text', async({ page }) => {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    // simple_effect_annotator exposes 15 attributes in the dropdown.
    await page.locator('#attributes-dropdown .dropdown-icon').click();
    await expect(page.locator('mat-option.attribute-option')).toHaveCount(15);

    // Typing runs a server-side search that narrows the option list.
    await Promise.all([
      page.locator('#attributes-dropdown input').fill('worst'),
      page.waitForResponse(resp => resp.url().includes('editor/annotator_attributes')),
    ]);

    const options = page.locator('mat-option.attribute-option');
    await expect(options).toHaveCount(3);
    // Options render as "<source> - <description>"; anchor on the " -" separator
    // so each source name matches exactly one option (they share the "worst_effect"
    // prefix otherwise).
    await Promise.all(
      ['worst_effect', 'worst_effect_genes', 'worst_effect_gene_list'].map(
        name => expect(options.filter({ hasText: `${name} -` })).toHaveCount(1)
      )
    );
  });

  test('should trim a whitespace-only attribute name to empty and flag empty duplicates', async({ page }) => {
    // Start from a clean pipeline so simple_effect's gene_list does not collide
    // with an existing pipeline attribute and pollute the validation state.
    await customDefaultPipeline(page);
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('simple_effect_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="gene_models-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/gene_models/GENCODE/46/basic/PRI').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('.attribute-source')).toHaveCount(3);

    // A whitespace-only name is trimmed to empty; a single empty name is accepted
    // (only duplicates are rejected), so Finish stays enabled.
    const firstName = page.locator('.editable-name').nth(0);
    await firstName.fill('   ');
    await firstName.blur();
    await expect(firstName).toHaveValue('');
    await expect(page.locator('mat-dialog-container .error-message')).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Finish' })).toBeEnabled();

    // Emptying a second name collides with the first (both ''): duplicate error.
    const secondName = page.locator('.editable-name').nth(1);
    await secondName.fill('  ');
    await secondName.blur();
    await expect(page.locator('mat-dialog-container .error-message'))
      .toContainText('Attribute with this name already exists');
    await expect(page.getByRole('button', { name: 'Finish' })).toBeDisabled();
  });

  test('should warn instead of selecting all when a resource has over 1000 attributes', async({ page }) => {
    // gene_set_annotator needs an input_gene_list; the default clinical pipeline
    // provides gene_list. The GO release exposes >1000 gene-set attributes.
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('gene_set_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="resource_id-dropdown"]').click();
    await page.locator('[id="resource_id-dropdown"] input').fill('GO_2024-06-17_release');
    await page.locator('mat-option', { hasText: 'gene_properties/gene_sets/GO_2024-06-17_release' }).first().click();
    await page.locator('[id="input_gene_list-dropdown"]').click();
    await page.locator('mat-option').getByText('gene_list').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await expect(page.locator('.attributes-section-label')).toContainText('(1)');

    // "Select all" is blocked with a performance warning; selection stays at 1.
    await page.getByRole('button', { name: 'Select all' }).click();
    await expect(page.locator('.warning-message')).toContainText('Selecting more than 1000 attributes');
    await expect(page.locator('.attributes-section-label')).toContainText('(1)');
  });

  test('should load more attributes when scrolling the attribute dropdown panel', async({ page }) => {
    // The GO release exposes >1000 attributes, so the attribute autocomplete
    // panel is paginated and loads more as it is scrolled.
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.locator('mat-option').getByText('gene_set_annotator').click();
    await page.getByRole('button', { name: 'Next' }).click();

    await page.locator('[id="resource_id-dropdown"]').click();
    await page.locator('[id="resource_id-dropdown"] input').fill('GO_2024-06-17_release');
    await page.locator('mat-option', { hasText: 'gene_properties/gene_sets/GO_2024-06-17_release' }).first().click();
    await page.locator('[id="input_gene_list-dropdown"]').click();
    await page.locator('mat-option').getByText('gene_list').click();
    await page.getByRole('button', { name: 'Next' }).click();

    // Open the attribute autocomplete panel and record the first page.
    await page.locator('#attributes-dropdown input').click();
    const options = page.locator('.mat-mdc-autocomplete-panel mat-option.attribute-option');
    await expect(options.first()).toBeVisible();
    const initialCount = await options.count();

    // Scrolling the panel to the bottom triggers loadMoreAttributes, which
    // appends the next page of options.
    await page.locator('.mat-mdc-autocomplete-panel').evaluate((el: HTMLElement) => el.scrollTo(0, el.scrollHeight));

    await expect.poll(() => options.count(), { timeout: 30000 }).toBeGreaterThan(initialCount);
  });
});

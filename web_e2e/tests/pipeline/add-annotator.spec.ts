import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';
import { AnnotatorDialog } from '../../pages/annotator.dialog';

test.describe('Add annotator to pipeline tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', {waitUntil: 'load'});

    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);

    await utils.loginUser(page, email, password);
    // wait for default pipeline to load
    await PipelineEditor.waitForLoaded(page);
  });

  test('should open new annotator dialog with correct header and first step', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();

    await expect(annotatorModal.container).toBeVisible();
    await expect(annotatorModal.header).toHaveText('New annotator');
    await expect(annotatorModal.annotatorDropdown).toBeVisible();
  });

  test('should append gene set annotator', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('gene_set_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('resource_id', 'gene_properties/gene_sets/autism');
    await annotatorModal.selectParameter('input_gene_list', 'gene_list');
    await annotatorModal.next();

    await expect(annotatorModal.attributeSources).toHaveCount(1);

    await Promise.all([
      annotatorModal.finish(),
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
    const annotatorModal = new AnnotatorDialog(page);
    await customDefaultPipeline(page);
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('liftover_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('chain', 'liftover/hg19_to_hs1');
    await annotatorModal.selectParameter('source_genome', 'hs1/genomes/ucsc-hs1');
    await annotatorModal.selectParameter('target_genome', 'hg38/genomes/GRCh38.p14');
    await annotatorModal.next();

    await Promise.all([
      annotatorModal.finish(),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/validate')
      ),
      page.waitForResponse(
        resp => resp.url().includes('api/pipelines/user'), // wait for pipeline to be saved
        {timeout: 20000}, // hg19_to_hs1 chain loading can take a while, increase timeout for this test
      ),
    ]);

    await PipelineEditor.waitForLoaded(page);

    await annotatorModal.open();

    await annotatorModal.selectAnnotator('position_score_annotator');
    await page.waitForTimeout(3000);
    await annotatorModal.next();

    await annotatorModal.selectParameter('resource_id', 'hg19/scores/FitCons2/E050');

    await annotatorModal.next();

    await Promise.all([
      annotatorModal.finish(),
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
      '    chain: liftover/hg19_to_hs1\n' +
      '    source_genome: hs1/genomes/ucsc-hs1\n' +
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
    const annotatorModal = new AnnotatorDialog(page);
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

    await PipelineEditor.waitForLoaded(page);
    // append new annotator
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('simple_effect_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');

    await annotatorModal.next();

    await Promise.all([
      annotatorModal.finish(),
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
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();

    await expect(annotatorModal.nextButton).toBeDisabled();

    await annotatorModal.selectAnnotator('allele_score');

    await expect(annotatorModal.nextButton).toBeEnabled();
  });

  test('should filter annotators in dropdown by search text', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();

    await annotatorModal.annotatorDropdown.fill('allele');
    await page.getByRole('combobox', { name: 'allele' }).dispatchEvent('input');

    await expect(page.locator('.annotator-option')).toHaveCount(2);
    await expect(page.locator('.annotator-option').filter({ hasText: 'allele_score_annotator' })).toBeVisible();
    await expect(page.locator('.annotator-option').filter({ hasText: 'normalize_allele_annotator' })).toBeVisible();
  });

  test('should navigate back from configure step to annotator selection step', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('allele_score');
    await annotatorModal.next();

    await expect(page.locator('[id="resource_id-dropdown"]')).toBeVisible();

    await annotatorModal.back();

    await expect(page.getByRole('combobox', { name: 'allele_score_annotator' })).toBeVisible();
  });

  test('should check selected data in summary panel', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('allele_score');
    await annotatorModal.next();

    // configure step: summary shows the selected annotator
    const configureSummary = page.locator('.mat-horizontal-stepper-content-current');
    await expect(configureSummary.locator('.annotator-display-text')).toContainText('annotator');
    await expect(configureSummary.locator('.annotator-display-text')).toContainText('allele_score_annotator');

    await annotatorModal.selectParameter('resource_id', 'hg38/scores/CADD_v1.7');
    await annotatorModal.next();

    // attribute step: summary adds the configured resource_id beneath the annotator
    const attributeSummary = page.locator('.mat-horizontal-stepper-content-current');
    await expect(attributeSummary.locator('.annotator-display-text')).toContainText('allele_score_annotator');
    const resourceIdDisplay = attributeSummary.locator('.resources-display-text').filter({ hasText: 'resource_id' });
    await expect(resourceIdDisplay).toContainText('hg38/scores/CADD_v1.7');
  });

  test('should remove a default attribute in the attribute step', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('simple_effect_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');
    await annotatorModal.next();

    await expect(annotatorModal.attributeSources).toHaveCount(3);

    await page.locator('#gene_list-remove-button').click();

    await expect(annotatorModal.attributeSources).toHaveCount(2);
  });

  test('should rename attribute and reflect new name in finished YAML', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await customDefaultPipeline(page);
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('simple_effect_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');
    await annotatorModal.next();

    await page.locator('.editable-name').first().fill('my_worst_effect');
    await page.locator('.editable-name').first().blur();

    await Promise.all([
      annotatorModal.finish(),
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
    const annotatorModal = new AnnotatorDialog(page);
    await utils.selectPipeline(page, 'pipeline/hg38_clinical_annotation');
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('simple_effect_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');
    await annotatorModal.next();

    await expect(annotatorModal.attributeSources).toHaveCount(3);

    await page.locator('.editable-name').first().fill('worst_effect_genes');
    await page.locator('.editable-name').first().blur();

    await expect(page.locator('.error-message')).toContainText('Attribute with this name already exists');
    await expect(annotatorModal.finishButton).toBeDisabled();
  });

  test('should disable New annotator button when pipeline config is invalid', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await new PipelineEditor(page).newPipeline();
    await utils.typeInPipelineEditor(page, 'preamble:\n input_reference_genome: hg38/genomes/GRCh38-hg38');
    await page.waitForSelector('.invalid-config', { state: 'visible', timeout: 120000 });

    await expect(annotatorModal.addAnnotatorButton).toBeDisabled();
  });

  test('should enable Next button on configure step only after all required fields are filled', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('liftover_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('chain', 'liftover/hg19_to_hs1');
    await expect(annotatorModal.nextButton).toBeDisabled();

    await annotatorModal.selectParameter('source_genome', 'hs1/genomes/ucsc-hs1');
    await expect(annotatorModal.nextButton).toBeDisabled();

    await annotatorModal.selectParameter('target_genome', 'hg38/genomes/GRCh38.p14');
    await expect(annotatorModal.nextButton).toBeEnabled();
  });

  test('should enable Next when only required field is filled', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    // effect_annotator has gene_models (required) and genome (optional resource)
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('effect_annotator', { exact: true });
    await annotatorModal.next();

    await expect(page.locator('[id="gene_models-dropdown"]')).toBeVisible();
    await expect(page.locator('[id="genome-dropdown"]')).toBeVisible();
    await expect(annotatorModal.nextButton).toBeDisabled();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');

    // Next is enabled without filling optional genome field
    await expect(annotatorModal.nextButton).toBeEnabled();
  });

  test('should keep Next enabled after filling an optional resource field', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    // effect_annotator: fill required gene_models then also fill optional genome
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('effect_annotator', { exact: true });
    await annotatorModal.next();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');
    await expect(annotatorModal.nextButton).toBeEnabled();

    await page.locator('[id="genome-dropdown"]').click();
    await page.locator('mat-option.resource-option').first().click();
    await expect(annotatorModal.nextButton).toBeEnabled();
  });

  test('should filter chain options by search text in configure step', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('liftover_annotator');
    await annotatorModal.next();

    await page.locator('[id="chain-dropdown"]').locator('.dropdown-icon').click();

    await page.locator('[id="chain-dropdown"] input').pressSequentially('hg19');
    const filteredOptions = page.locator('mat-option.resource-option');

    expect(await filteredOptions.count()).toBe(4);
  });

  test('should filter source_genome options by search text in configure step', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('liftover_annotator');
    await annotatorModal.next();

    await page.locator('[id="source_genome-dropdown"]').locator('.dropdown-icon').click();

    await page.locator('[id="source_genome-dropdown"] input').pressSequentially('hs1');
    const filteredOptions = page.locator('mat-option.resource-option');

    expect(await filteredOptions.count()).toBe(1);
  });

  test('should disable Next button in configure step when a filled required field is cleared', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('liftover_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('chain', 'liftover/hg19_to_hs1');
    await annotatorModal.selectParameter('source_genome', 'hs1/genomes/ucsc-hs1');
    await annotatorModal.selectParameter('target_genome', 'hg38/genomes/GRCh38.p14');

    await expect(annotatorModal.nextButton).toBeEnabled();

    // clicking the dropdown icon clears the field and opens the panel
    await page.locator('[id="chain-dropdown"]').locator('.dropdown-icon').click();
    await page.keyboard.press('Escape');

    await expect(annotatorModal.nextButton).toBeDisabled();
  });

  test('should toggle attribute internal flag and reflect it in finished YAML', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await customDefaultPipeline(page);
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('simple_effect_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');
    await annotatorModal.next();

    // gene_list is the 3rd attribute (index 2) and internal: true by default
    const geneListCheckbox = page.locator('.attribute-internal input[type="checkbox"]').nth(2);
    await expect(geneListCheckbox).toBeChecked();
    await geneListCheckbox.click();
    await expect(geneListCheckbox).not.toBeChecked();

    await Promise.all([
      annotatorModal.finish(),
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
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();

    await annotatorModal.selectAnnotator('simple_effect_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');
    await annotatorModal.next();

    await expect(annotatorModal.attributeSources).toHaveCount(3);

    await page.getByRole('button', { name: 'Select all' }).click();
    await expect(page.locator('.attributes-section-label')).toContainText('(15)');

    await page.getByRole('button', { name: 'Remove all' }).click();
    await expect(page.locator('.attributes-section-label')).not.toBeVisible();

    await annotatorModal.openAttributeDropdown();
    await annotatorModal.attributeOptions.first().click();

    await expect(annotatorModal.attributeSources).toHaveCount(1);
    await expect(annotatorModal.attributeSources.first()).toHaveText('worst_effect');
  });

  test('should filter attributes in the dropdown by search text', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('simple_effect_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');
    await annotatorModal.next();

    // simple_effect_annotator exposes 15 attributes in the dropdown.
    await annotatorModal.openAttributeDropdown();
    await expect(annotatorModal.attributeOptions).toHaveCount(15);

    // Typing runs a server-side search that narrows the option list.
    await Promise.all([
      annotatorModal.attributeInput.fill('worst'),
      page.waitForResponse(resp => resp.url().includes('editor/annotator_attributes')),
    ]);

    const options = annotatorModal.attributeOptions;
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
    const annotatorModal = new AnnotatorDialog(page);
    // Start from a clean pipeline so simple_effect's gene_list does not collide
    // with an existing pipeline attribute and pollute the validation state.
    await customDefaultPipeline(page);
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('simple_effect_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('gene_models', 'hg38/gene_models/GENCODE/46/basic/PRI');
    await annotatorModal.next();

    await expect(annotatorModal.attributeSources).toHaveCount(3);

    // A whitespace-only name is trimmed to empty; a single empty name is accepted
    // (only duplicates are rejected), so Finish stays enabled.
    const firstName = page.locator('.editable-name').nth(0);
    await firstName.fill('   ');
    await firstName.blur();
    await expect(firstName).toHaveValue('');
    await expect(page.locator('mat-dialog-container .error-message')).toHaveCount(0);
    await expect(annotatorModal.finishButton).toBeEnabled();

    // Emptying a second name collides with the first (both ''): duplicate error.
    const secondName = page.locator('.editable-name').nth(1);
    await secondName.fill('  ');
    await secondName.blur();
    await expect(page.locator('mat-dialog-container .error-message'))
      .toContainText('Attribute with this name already exists');
    await expect(annotatorModal.finishButton).toBeDisabled();
  });

  test('should warn instead of selecting all when a resource has over 1000 attributes', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    // gene_set_annotator needs an input_gene_list; the default clinical pipeline
    // provides gene_list. The GO release exposes >1000 gene-set attributes.
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('gene_set_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter(
      'resource_id',
      'gene_properties/gene_sets/GO_2024-06-17_release',
      { search: 'GO_2024-06-17_release' }
    );
    await annotatorModal.selectParameter('input_gene_list', 'gene_list');
    await annotatorModal.next();

    await expect(page.locator('.attributes-section-label')).toContainText('(1)');

    // "Select all" is blocked with a performance warning; selection stays at 1.
    await page.getByRole('button', { name: 'Select all' }).click();
    await expect(page.locator('.warning-message')).toContainText('Selecting more than 1000 attributes');
    await expect(page.locator('.attributes-section-label')).toContainText('(1)');
  });

  test('should load more attributes when scrolling the attribute dropdown panel', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    // The GO release exposes >1000 attributes, so the attribute autocomplete
    // panel is paginated and loads more as it is scrolled.
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('gene_set_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter(
      'resource_id',
      'gene_properties/gene_sets/GO_2024-06-17_release',
      { search: 'GO_2024-06-17_release' }
    );
    await annotatorModal.selectParameter('input_gene_list', 'gene_list');
    await annotatorModal.next();

    // Open the attribute autocomplete panel and record the first page.
    await annotatorModal.attributeInput.click();
    const options = page.locator('.mat-mdc-autocomplete-panel mat-option.attribute-option');
    await expect(options.first()).toBeVisible();
    const initialCount = await options.count();

    // Scrolling the panel to the bottom triggers loadMoreAttributes, which
    // appends the next page of options.
    await page.locator('.mat-mdc-autocomplete-panel').evaluate((el: HTMLElement) => el.scrollTo(0, el.scrollHeight));

    await expect.poll(() => options.count(), { timeout: 30000 }).toBeGreaterThan(initialCount);
  });
});

import { test, expect, Page } from '@playwright/test';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';

test.describe('Pipeline aggregators tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await page.waitForSelector('.loaded-editor', { state: 'visible', timeout: 120000 });
  });

  // Walk the New annotator dialog for a score resource up to the
  // "Configure aggregation" step. The final Next triggers the
  // annotator_aggregators request that populates the aggregators grid.
  async function openAggregatorsStep(page: Page, annotator: string, resourceId: string): Promise<void> {
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();

    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.getByRole('combobox', { name: 'Select annotator' }).fill(annotator);
    await page.locator('mat-option').getByText(annotator).first().click();
    await page.locator('.mat-horizontal-stepper-content-current button.next-button').click();

    await page.locator('[id="resource_id-dropdown"]').click();
    await page.locator('[id="resource_id-dropdown"] input').fill(resourceId);
    await page.locator('mat-option', { hasText: resourceId }).first().click();
    await page.locator('.mat-horizontal-stepper-content-current button.next-button').click();

    // Wait for the attributes step to populate before advancing; the aggregators
    // request maps over the selected attributes, so it must not fire empty.
    await expect(page.locator('.attribute-source').first()).toBeVisible();

    // attributes step -> aggregators step
    await Promise.all([
      page.locator('.mat-horizontal-stepper-content-current button.next-button').click(),
      page.waitForResponse(resp => resp.url().includes('editor/annotator_aggregators')),
    ]);
    await expect(page.locator('#attributes-aggregators-list')).toBeVisible();
  }

  test('should show default attributes, float types and max aggregator for CADD', async({ page }) => {
    await openAggregatorsStep(page, 'allele_score', 'hg38/scores/CADD_v1.7');

    const names = page.locator('#attributes-aggregators-list .attribute-name-main');
    await expect(names).toHaveText(['cadd_raw', 'cadd_phred']);

    const types = page.locator('#attributes-aggregators-list .data-type-badge');
    await expect(types).toHaveText(['float', 'float']);

    // Float attributes default to the "max" aggregator.
    const aggregators = page.locator('#attributes-aggregators-list .aggregator mat-select');
    await expect(aggregators.nth(0)).toContainText('max (default)');
    await expect(aggregators.nth(1)).toContainText('max (default)');
  });

  test('should show str types and list aggregator for ClinVar', async({ page }) => {
    await openAggregatorsStep(page, 'allele_score', 'hg38/scores/ClinVar_20240730');

    const names = page.locator('#attributes-aggregators-list .attribute-name-main');
    await expect(names).toHaveText(['CLNDN', 'CLNSIG']);

    const types = page.locator('#attributes-aggregators-list .data-type-badge');
    await expect(types).toHaveText(['str', 'str']);

    // String attributes default to the "list" aggregator (contrast with CADD).
    const aggregators = page.locator('#attributes-aggregators-list .aggregator mat-select');
    await expect(aggregators.nth(0)).toContainText('list (default)');
    await expect(aggregators.nth(1)).toContainText('list (default)');
  });

  test('should list the aggregator options and mark exactly one default for a float attribute', async({ page }) => {
    await openAggregatorsStep(page, 'allele_score', 'hg38/scores/CADD_v1.7');

    await page.locator('#attributes-aggregators-list .aggregator mat-select').nth(0).click();

    const options = page.locator('mat-option.aggregator-option');
    // The default option is labelled "max (default)"; the others are plain names.
    await expect(options.filter({ hasText: '(default)' })).toHaveCount(1);
    await expect(options.filter({ hasText: 'max (default)' })).toHaveCount(1);
    await Promise.all(
      ['min', 'mean', 'median', 'concatenate'].map(
        name => expect(options.filter({ hasText: name })).toHaveCount(1)
      )
    );
  });

  test('should write the selected aggregator into the finished YAML', async({ page }) => {
    await openAggregatorsStep(page, 'allele_score', 'hg38/scores/CADD_v1.7');

    // Change the first attribute (cadd_raw) from the default "max" to "mean".
    await page.locator('#attributes-aggregators-list .aggregator mat-select').nth(0).click();
    await page.locator('mat-option.aggregator-option').getByText('mean', { exact: true }).click();

    await Promise.all([
      page.locator('.mat-horizontal-stepper-content-current button.finish-button').click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/validate')),
    ]);

    /* eslint-disable */
    const value = await page.evaluate(() => {
      return (window as any).monaco.editor.getModels()[0].getValue();
    });
    /* eslint-enable */

    // The changed aggregator and the untouched default are both serialised.
    expect(value).toContain(
      '- allele_score_annotator:\n' +
      '    resource_id: hg38/scores/CADD_v1.7\n' +
      '    attributes:\n' +
      '    - name: cadd_raw\n' +
      '      source: cadd_raw\n' +
      '      internal: false\n' +
      '      aggregator: mean\n' +
      '    - name: cadd_phred\n' +
      '      source: cadd_phred\n' +
      '      internal: false\n' +
      '      aggregator: max\n'
    );
  });

  test('should default to mean and write a join aggregator with separator for phastCons100way', async({ page }) => {
    // phastCons100way is a position_score resource with a single float attribute.
    await openAggregatorsStep(page, 'position_score_annotator', 'hg38/scores/phastCons100way');

    await expect(page.locator('#attributes-aggregators-list .attribute-name-main')).toHaveText('phastcons100way');
    await expect(page.locator('#attributes-aggregators-list .attribute-source-description'))
      .toHaveText('phastCons100way');
    await expect(page.locator('#attributes-aggregators-list .data-type-badge')).toHaveText('float');

    const aggregator = page.locator('#attributes-aggregators-list .aggregator mat-select');
    await expect(aggregator).toContainText('mean (default)');

    // Switch to the parametrized "join" aggregator; a separator field appears
    // pre-filled with the default separator ",".
    await aggregator.click();
    await page.locator('mat-option.aggregator-option').getByText('join', { exact: true }).click();
    await expect(aggregator).toContainText('join');
    await expect(page.locator('#attributes-aggregators-list .separator-field')).toHaveValue(',');

    await Promise.all([
      page.locator('.mat-horizontal-stepper-content-current button.finish-button').click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/validate')),
    ]);

    /* eslint-disable */
    const value = await page.evaluate(() => {
      return (window as any).monaco.editor.getModels()[0].getValue();
    });
    /* eslint-enable */

    // The parametrized aggregator serialises as "join(<separator>)".
    expect(value).toContain(
      '- position_score_annotator:\n' +
      '    resource_id: hg38/scores/phastCons100way\n' +
      '    attributes:\n' +
      '    - name: phastcons100way\n' +
      '      source: phastCons100way\n' +
      '      internal: false\n' +
      '      aggregator: join(,)\n'
    );
  });

  test('should write per-attribute aggregators when changing several at once', async({ page }) => {
    // CADD has two float attributes; give each a different aggregator and verify
    // both land in the pipeline text, including a custom join separator.
    await openAggregatorsStep(page, 'allele_score', 'hg38/scores/CADD_v1.7');

    const aggregators = page.locator('#attributes-aggregators-list .aggregator mat-select');

    // cadd_raw -> min
    await aggregators.nth(0).click();
    await page.locator('mat-option.aggregator-option').getByText('min', { exact: true }).click();

    // cadd_phred -> join, then change the separator from the default "," to ";"
    await aggregators.nth(1).click();
    await page.locator('mat-option.aggregator-option').getByText('join', { exact: true }).click();
    const separator = page.locator('#attributes-aggregators-list .separator-field');
    await expect(separator).toHaveValue(',');
    await separator.fill(';');

    await Promise.all([
      page.locator('.mat-horizontal-stepper-content-current button.finish-button').click(),
      page.waitForResponse(resp => resp.url().includes('api/pipelines/validate')),
    ]);

    /* eslint-disable */
    const value = await page.evaluate(() => {
      return (window as any).monaco.editor.getModels()[0].getValue();
    });
    /* eslint-enable */

    expect(value).toContain(
      '- allele_score_annotator:\n' +
      '    resource_id: hg38/scores/CADD_v1.7\n' +
      '    attributes:\n' +
      '    - name: cadd_raw\n' +
      '      source: cadd_raw\n' +
      '      internal: false\n' +
      '      aggregator: min\n' +
      '    - name: cadd_phred\n' +
      '      source: cadd_phred\n' +
      '      internal: false\n' +
      '      aggregator: join(;)\n'
    );
  });

  test('should disable the aggregator for a non-aggregatable annotatable attribute', async({ page }) => {
    // normalize_allele_annotator produces a single "annotatable"-typed attribute,
    // which has no aggregators. Use a clean pipeline so normalized_allele does not
    // collide with the default clinical pipeline's existing attribute.
    await customDefaultPipeline(page);
    await page.locator('#pipeline-actions').locator('#add-annotator-button').click();
    await page.getByRole('combobox', { name: 'Select annotator' }).click();
    await page.getByRole('combobox', { name: 'Select annotator' }).fill('normalize_allele');
    await page.locator('mat-option').getByText('normalize_allele_annotator').click();
    await page.locator('.mat-horizontal-stepper-content-current button.next-button').click();

    await page.locator('[id="genome-dropdown"]').click();
    await page.locator('mat-option').getByText('hg38/genomes/GRCh38.p14').click();
    await page.locator('.mat-horizontal-stepper-content-current button.next-button').click();

    await expect(page.locator('.attribute-source').first()).toBeVisible();
    await Promise.all([
      page.locator('.mat-horizontal-stepper-content-current button.next-button').click(),
      page.waitForResponse(resp => resp.url().includes('editor/annotator_aggregators')),
    ]);

    await expect(page.locator('#attributes-aggregators-list .data-type-badge')).toHaveText('annotatable');
    const aggregator = page.locator('#attributes-aggregators-list .aggregator mat-select');
    await expect(aggregator).toContainText('No aggregation');
    await expect(aggregator).toHaveAttribute('aria-disabled', 'true');
  });

  test('should preserve attributes on Back and recompute the aggregators grid when the set changes',
    async({ page }) => {
      await openAggregatorsStep(page, 'allele_score', 'hg38/scores/CADD_v1.7');

      await expect(page.locator('#attributes-aggregators-list .attribute-name-main'))
        .toHaveText(['cadd_raw', 'cadd_phred']);

      // Back to the attributes step keeps the previously selected attributes.
      await page.locator('.mat-horizontal-stepper-content-current button.back-button').click();
      await expect(page.locator('.attribute-source')).toHaveText(['cadd_raw', 'cadd_phred']);

      // Remove one attribute and return: the aggregators grid reflects the new set.
      await page.locator('#cadd_phred-remove-button').click();
      await expect(page.locator('.attribute-source')).toHaveText(['cadd_raw']);

      await Promise.all([
        page.locator('.mat-horizontal-stepper-content-current button.next-button').click(),
        page.waitForResponse(resp => resp.url().includes('editor/annotator_aggregators')),
      ]);
      await expect(page.locator('#attributes-aggregators-list .attribute-name-main')).toHaveText('cadd_raw');
    });
});

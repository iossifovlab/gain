import { test, expect, Page } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import * as utils from '../../utils';
import { customDefaultPipeline } from './helpers';
import { AnnotatorDialog } from '../../pages/annotator.dialog';

test.describe('Pipeline aggregators tests', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
  });

  // Walk the New annotator dialog for a score resource up to the
  // "Configure aggregation" step. The final Next triggers the
  // annotator_aggregators request that populates the aggregators grid.
  async function openAggregatorsStep(
    page: Page,
    annotatorModal: AnnotatorDialog,
    annotator: string,
    resourceId: string
  ): Promise<void> {
    await annotatorModal.open();
    await annotatorModal.selectAnnotator(annotator);
    await annotatorModal.next();

    await annotatorModal.selectParameter('resource_id', resourceId, { search: resourceId });
    await annotatorModal.next();

    // Wait for the attributes step to populate before advancing; the aggregators
    // request maps over the selected attributes, so it must not fire empty.
    await expect(annotatorModal.attributeSources.first()).toBeVisible();

    // attributes step -> aggregators step
    await Promise.all([
      annotatorModal.next(),
      page.waitForResponse(resp => resp.url().includes('editor/annotator_aggregators')),
    ]);
    await expect(annotatorModal.aggregatorsList).toBeVisible();
  }

  test('should show default attributes, float types and max aggregator for CADD', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await openAggregatorsStep(page, annotatorModal, 'allele_score', 'hg38/scores/CADD_v1.7');

    await expect(annotatorModal.aggregatorNames).toHaveText(['cadd_raw', 'cadd_phred']);
    await expect(annotatorModal.aggregatorTypes).toHaveText(['float', 'float']);

    // Float attributes default to the "max" aggregator.
    await expect(annotatorModal.aggregatorSelects.nth(0)).toContainText('max (default)');
    await expect(annotatorModal.aggregatorSelects.nth(1)).toContainText('max (default)');
  });

  test('should show str types and list aggregator for ClinVar', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await openAggregatorsStep(page, annotatorModal, 'allele_score', 'hg38/scores/ClinVar_20240730');

    await expect(annotatorModal.aggregatorNames).toHaveText(['CLNDN', 'CLNSIG']);
    await expect(annotatorModal.aggregatorTypes).toHaveText(['str', 'str']);

    // String attributes default to the "list" aggregator (contrast with CADD).
    await expect(annotatorModal.aggregatorSelects.nth(0)).toContainText('list (default)');
    await expect(annotatorModal.aggregatorSelects.nth(1)).toContainText('list (default)');
  });

  test('should list the aggregator options and mark exactly one default for a float attribute', async({ page }) => {
    const annotatorModal = new AnnotatorDialog(page);
    await openAggregatorsStep(page, annotatorModal, 'allele_score', 'hg38/scores/CADD_v1.7');

    await annotatorModal.aggregatorSelects.nth(0).click();

    const options = annotatorModal.aggregatorOptions;
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
    const annotatorModal = new AnnotatorDialog(page);
    await openAggregatorsStep(page, annotatorModal, 'allele_score', 'hg38/scores/CADD_v1.7');

    // Change the first attribute (cadd_raw) from the default "max" to "mean".
    await annotatorModal.selectAggregator(0, 'mean');

    await Promise.all([
      annotatorModal.finish(),
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
    const annotatorModal = new AnnotatorDialog(page);
    // phastCons100way is a position_score resource with a single float attribute.
    await openAggregatorsStep(page, annotatorModal, 'position_score_annotator', 'hg38/scores/phastCons100way');

    await expect(annotatorModal.aggregatorNames).toHaveText('phastcons100way');
    await expect(page.locator('#attributes-aggregators-list .attribute-source-description'))
      .toHaveText('phastCons100way');
    await expect(annotatorModal.aggregatorTypes).toHaveText('float');

    const aggregator = annotatorModal.aggregatorSelects;
    await expect(aggregator).toContainText('mean (default)');

    // Switch to the parametrized "join" aggregator; a separator field appears
    // pre-filled with the default separator ",".
    await annotatorModal.selectAggregator(0, 'join');
    await expect(aggregator).toContainText('join');
    await expect(annotatorModal.separatorField).toHaveValue(',');

    await Promise.all([
      annotatorModal.finish(),
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
    const annotatorModal = new AnnotatorDialog(page);
    // CADD has two float attributes; give each a different aggregator and verify
    // both land in the pipeline text, including a custom join separator.
    await openAggregatorsStep(page, annotatorModal, 'allele_score', 'hg38/scores/CADD_v1.7');

    // cadd_raw -> min
    await annotatorModal.selectAggregator(0, 'min');

    // cadd_phred -> join, then change the separator from the default "," to ";"
    await annotatorModal.selectAggregator(1, 'join');
    const separator = annotatorModal.separatorField;
    await expect(separator).toHaveValue(',');
    await separator.fill(';');

    await Promise.all([
      annotatorModal.finish(),
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
    const annotatorModal = new AnnotatorDialog(page);
    // normalize_allele_annotator produces a single "annotatable"-typed attribute,
    // which has no aggregators. Use a clean pipeline so normalized_allele does not
    // collide with the default clinical pipeline's existing attribute.
    await customDefaultPipeline(page);
    await annotatorModal.open();
    await annotatorModal.selectAnnotator('normalize_allele_annotator');
    await annotatorModal.next();

    await annotatorModal.selectParameter('genome', 'hg38/genomes/GRCh38.p14');
    await annotatorModal.next();

    await expect(annotatorModal.attributeSources.first()).toBeVisible();
    await Promise.all([
      annotatorModal.next(),
      page.waitForResponse(resp => resp.url().includes('editor/annotator_aggregators')),
    ]);

    await expect(annotatorModal.aggregatorTypes).toHaveText('annotatable');
    const aggregator = annotatorModal.aggregatorSelects;
    await expect(aggregator).toContainText('No aggregation');
    await expect(aggregator).toHaveAttribute('aria-disabled', 'true');
  });

  test('should preserve attributes on Back and recompute the aggregators grid when the set changes',
    async({ page }) => {
      const annotatorModal = new AnnotatorDialog(page);
      await openAggregatorsStep(page, annotatorModal, 'allele_score', 'hg38/scores/CADD_v1.7');

      await expect(annotatorModal.aggregatorNames).toHaveText(['cadd_raw', 'cadd_phred']);

      // Back to the attributes step keeps the previously selected attributes.
      await annotatorModal.back();
      await expect(annotatorModal.attributeSources).toHaveText(['cadd_raw', 'cadd_phred']);

      // Remove one attribute and return: the aggregators grid reflects the new set.
      await page.locator('#cadd_phred-remove-button').click();
      await expect(annotatorModal.attributeSources).toHaveText(['cadd_raw']);

      await Promise.all([
        annotatorModal.next(),
        page.waitForResponse(resp => resp.url().includes('editor/annotator_aggregators')),
      ]);
      await expect(annotatorModal.aggregatorNames).toHaveText('cadd_raw');
    });
});

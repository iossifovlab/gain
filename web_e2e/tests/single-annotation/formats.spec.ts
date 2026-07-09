import { test, expect } from '@playwright/test';
import { PipelineEditor } from '../../pages/pipeline-editor.page';
import { SingleAnnotation } from '../../pages/single-annotation.page';
import * as utils from '../../utils';
import { effectAnnotatorPipeline, customDefaultPipeline } from './helpers';

test.describe('Single annotation annotatable formats and report features', () => {
  test.beforeEach(async({ page }) => {
    await page.goto('/', { waitUntil: 'load' });
    const email = utils.getRandomString() + '@email.com';
    const password = 'aaabbb';
    await utils.registerUser(page, email, password);
    await utils.loginUser(page, email, password);
    await PipelineEditor.waitForLoaded(page);
  });

  test('should display position-start and position-end for region annotatable', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await effectAnnotatorPipeline(page);
    await singleAnnotation.annotate('chr1 11796321 11800000');

    await expect(singleAnnotation.positionStart).toBeVisible();
    await expect(singleAnnotation.positionEnd).toBeVisible();
    await expect(singleAnnotation.positionStart).toHaveText('11796321');
    await expect(singleAnnotation.positionEnd).toHaveText('11800000');
    await expect(singleAnnotation.reference).not.toBeVisible();
    await expect(singleAnnotation.alternate).not.toBeVisible();
  });

  test('should display position for position-only annotatable', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await effectAnnotatorPipeline(page);
    await singleAnnotation.annotate('chr1 11796321');

    await expect(singleAnnotation.position).toBeVisible();
    await expect(singleAnnotation.position).toHaveText('11796321');
    await expect(singleAnnotation.positionStart).not.toBeVisible();
    await expect(singleAnnotation.positionEnd).not.toBeVisible();
    await expect(singleAnnotation.reference).not.toBeVisible();
    await expect(singleAnnotation.alternate).not.toBeVisible();
  });

  test('should render effect table in full report mode', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await effectAnnotatorPipeline(page);
    await singleAnnotation.annotate('chr1 11796321 G A');

    await expect(singleAnnotation.compactValueResults.nth(0)).toHaveText('MTHFR:missense');
    await expect(singleAnnotation.compactValueResults.nth(1)).toHaveText(
      'ENST00000376590.9:MTHFR:missense:222/656(Ala->Val)'
    );
    await singleAnnotation.toggleFullReport();
    await expect(singleAnnotation.attributeContainers.nth(0).locator('app-effect-table')).toBeVisible();
    await expect(singleAnnotation.attributeContainers.nth(1).locator('app-effect-table')).toBeVisible();
  });

  test('should render histogram in report body in full report mode', async({ page }) => {
    const singleAnnotation = new SingleAnnotation(page);
    await customDefaultPipeline(page);
    await singleAnnotation.annotate('chr1 11796321 G A');

    await expect(singleAnnotation.attributeContainers.nth(1).locator('app-histogram-wrapper')).not.toBeVisible();
    await singleAnnotation.toggleFullReport();
    await expect(singleAnnotation.attributeContainers.nth(1).locator('app-histogram-wrapper')).toBeVisible();
  });
});
